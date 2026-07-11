import asyncio
from collections import deque
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
import sys
import threading
from typing import IO
from uuid import uuid4

from loguru import logger
from pydantic import TypeAdapter

from agent_server.agent.processes.managed_worker_process import ManagedWorkerProcess
from agent_server.schemas.activity import ActivityCreatedEvent, ClientEvent, ErrorActivity, StatusEvent, StreamingEvent

_STREAMING_EVENT_ADAPTER = TypeAdapter(StreamingEvent)
_RESTART_BACKOFF_INITIAL_SECONDS = 1.0
_RESTART_BACKOFF_MAX_SECONDS = 30.0
_RESTART_BACKOFF_RESET_SECONDS = 60.0


@dataclass(slots=True)
class _RunningWorker:
    """Owns the process and supporting tasks for one worker run so they are always cleaned up together."""

    process: ManagedWorkerProcess
    wait_task: asyncio.Task[int]
    stdout_thread: threading.Thread
    stderr_thread: threading.Thread
    stderr_lines: list[str]
    writer_task: asyncio.Task[None] | None = None
    cancel_requested: bool = False
    _closed: bool = field(default=False, init=False)

    async def wait(self) -> int:
        return await asyncio.shield(self.wait_task)

    async def terminate(self) -> None:
        self.process.terminate_tree()
        await self.wait()

    async def close(self) -> None:
        if self._closed:
            return
        try:
            self.process.terminate_tree()
        finally:
            await self._finish_cleanup()
        self._closed = True

    async def _finish_cleanup(self) -> None:
        try:
            await self.wait()
        finally:
            await self._close_io()

    async def _close_io(self) -> None:
        try:
            if self.writer_task is not None:
                self.writer_task.cancel()
                with suppress(asyncio.CancelledError):
                    await self.writer_task
        finally:
            try:
                await self._join_thread(self.stdout_thread)
                await self._join_thread(self.stderr_thread)
            finally:
                self.process.close()

    @staticmethod
    async def _join_thread(thread: threading.Thread) -> None:
        if thread.ident is not None:
            await asyncio.to_thread(thread.join)


@dataclass(frozen=True, slots=True)
class _WorkerRunResult:
    became_ready: bool
    returncode: int
    runtime_seconds: float
    cancel_requested: bool
    stderr_lines: tuple[str, ...]


class AgentManager:
    def __init__(
        self,
        streaming_events: asyncio.Queue[StreamingEvent],
        working_dir: Path,
        session_database: Path | None = None,
        agent_id: str = "main",
        process_tree_root: bool = True,
    ) -> None:

        self._streaming_events = streaming_events
        self._working_dir = working_dir
        self._session_database = session_database
        self.agent_id = agent_id
        # On POSIX, root managers create the process group while sub-agent managers join it for recursive cancellation.
        self._process_tree_root = process_tree_root

        # A list of user activities that have not been forwarded to the agent yet.
        self._client_events: deque[ClientEvent] = deque()
        # This indicates whether there are pending activities that need to be forwarded. This is used to avoid polling on the pending queue.
        self._has_client_events = asyncio.Event()
        self._worker: _RunningWorker | None = None
        self._shutdown_event = asyncio.Event()

    async def submit_event(self, event: ClientEvent) -> None:
        """Forwards a client event to the running agent."""
        self._client_events.append(event)
        self._has_client_events.set()

    async def cancel(self) -> None:
        """Immediately kills the agent and any of its sub-agents."""
        worker = self._worker
        if worker is not None and worker.process.poll() is None:
            self._client_events.clear()
            self._has_client_events.clear()
            worker.cancel_requested = True
            await self._streaming_events.put(StatusEvent(agent_id=self.agent_id, status_id="agent_cancelling"))
            await worker.terminate()

    async def shutdown(self) -> None:
        """Stops the agent subprocess and prevents it from restarting."""
        shutdown_started = not self._shutdown_event.is_set()
        self._shutdown_event.set()
        self._client_events.clear()
        self._has_client_events.clear()

        worker = self._worker
        if worker is not None:
            if shutdown_started and worker.process.poll() is None:
                await self._streaming_events.put(StatusEvent(agent_id=self.agent_id, status_id="agent_stopping"))
            await worker.terminate()

    async def start_manager(self) -> None:
        """Starts the agent subprocess and manages its lifecycle. If the agent exits, it will be restarted."""
        restart_delay = _RESTART_BACKOFF_INITIAL_SECONDS
        try:
            while not self._shutdown_event.is_set():
                logger.info("Starting agent subprocess")
                await self._streaming_events.put(StatusEvent(agent_id=self.agent_id, status_id="agent_starting"))
                result = await self._run_worker_once()
                await self._report_worker_exit(result)

                if self._shutdown_event.is_set():
                    return
                if result.cancel_requested:
                    restart_delay = _RESTART_BACKOFF_INITIAL_SECONDS
                    continue
                if result.became_ready and result.runtime_seconds >= _RESTART_BACKOFF_RESET_SECONDS:
                    restart_delay = _RESTART_BACKOFF_INITIAL_SECONDS

                logger.warning("Restarting agent subprocess in {} seconds", restart_delay)
                try:
                    await asyncio.wait_for(self._shutdown_event.wait(), timeout=restart_delay)
                except TimeoutError:
                    restart_delay = min(restart_delay * 2, _RESTART_BACKOFF_MAX_SECONDS)
                else:
                    return
        except asyncio.CancelledError:
            self._shutdown_event.set()
            logger.info("Agent subprocess exited due to shutdown")
            await self._streaming_events.put(StatusEvent(agent_id=self.agent_id, status_id="agent_stopped"))
            raise

    async def _run_worker_once(self) -> _WorkerRunResult:
        loop = asyncio.get_running_loop()
        started_at = loop.time()
        worker_ready = asyncio.Event()
        worker = await self._start_worker(worker_ready)
        try:
            became_ready = await self._wait_for_worker_ready_or_exit(worker_ready, worker.wait_task)
            if became_ready:
                # Do not forward queued events until the worker has initialized and can consume them.
                worker.writer_task = asyncio.create_task(self._pump_stdin(worker))
            returncode = await worker.wait()
        finally:
            await worker.close()
            if self._worker is worker:
                self._worker = None

        return _WorkerRunResult(
            became_ready=became_ready,
            returncode=returncode,
            runtime_seconds=loop.time() - started_at,
            cancel_requested=worker.cancel_requested,
            stderr_lines=tuple(worker.stderr_lines),
        )

    async def _start_worker(self, worker_ready: asyncio.Event) -> _RunningWorker:
        process = ManagedWorkerProcess.start(
            self._worker_command(),
            process_tree_root=self._process_tree_root,
        )
        stderr_lines: list[str] = []
        loop = asyncio.get_running_loop()
        worker = _RunningWorker(
            process=process,
            wait_task=asyncio.create_task(asyncio.to_thread(process.wait)),
            stdout_thread=threading.Thread(
                target=self._read_stdout,
                args=(process, loop, worker_ready),
                name="agent-stdout",
                daemon=True,
            ),
            stderr_thread=threading.Thread(
                target=self._read_stderr,
                args=(process, stderr_lines),
                name="agent-stderr",
                daemon=True,
            ),
            stderr_lines=stderr_lines,
        )
        self._worker = worker
        try:
            worker.stdout_thread.start()
            worker.stderr_thread.start()
        except Exception:
            await worker.close()
            self._worker = None
            raise
        return worker

    def _worker_command(self) -> list[str]:
        command = [
            sys.executable,
            "-m",
            "agent_server.agent.agent_worker",
            "--working-dir",
            str(self._working_dir),
            "--agent-id",
            self.agent_id,
            "--managed",
        ]
        if self._session_database is not None:
            command.extend(["--session-database", str(self._session_database)])
        return command

    async def _report_worker_exit(self, result: _WorkerRunResult) -> None:
        if self._shutdown_event.is_set():
            logger.info("Agent subprocess exited due to shutdown")
            await self._streaming_events.put(StatusEvent(agent_id=self.agent_id, status_id="agent_stopped"))
        elif result.cancel_requested:
            logger.info("Agent subprocess exited due to cancellation")
            await self._streaming_events.put(StatusEvent(agent_id=self.agent_id, status_id="agent_cancelled"))
        elif result.returncode != 0:
            logger.error("Agent subprocess crashed (exit code {})", result.returncode)
            detail = (
                "\n".join(result.stderr_lines)
                if result.stderr_lines
                else f"Process exited with code {result.returncode}"
            )
            await self._streaming_events.put(
                ActivityCreatedEvent(
                    agent_id=self.agent_id,
                    activity=ErrorActivity(
                        id=str(uuid4()),
                        agent_id=self.agent_id,
                        state="error",
                        error_type="agent_error",
                        detail=detail,
                    ),
                )
            )
        else:
            logger.info("Agent subprocess exited normally")

    @staticmethod
    async def _wait_for_worker_ready_or_exit(
        worker_ready: asyncio.Event,
        process_wait_task: asyncio.Task[int],
    ) -> bool:
        ready_task = asyncio.create_task(worker_ready.wait())
        try:
            done, _ = await asyncio.wait(
                {ready_task, process_wait_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            return ready_task in done and worker_ready.is_set()
        finally:
            if not ready_task.done():
                ready_task.cancel()
            with suppress(asyncio.CancelledError):
                await ready_task

    async def _pump_stdin(self, worker: _RunningWorker) -> None:
        """Forwards pending user events to the running agent's stdin."""
        process = worker.process
        stdin = process.stdin
        assert stdin is not None
        try:
            while True:
                await self._has_client_events.wait()
                # Clear before draining so a concurrent append + set during the drain leaves the event set for the next iteration rather than being lost.
                self._has_client_events.clear()
                events: list[ClientEvent] = []
                while self._client_events:
                    events.append(self._client_events.popleft())
                if events:
                    payload = "".join(event.model_dump_json() + "\n" for event in events).encode()
                    write_succeeded = await asyncio.to_thread(self._write_stdin, stdin, payload)
                    if not write_succeeded:
                        # Preserve events across unexpected worker exits so the next worker can process them.
                        if not worker.cancel_requested and not self._shutdown_event.is_set():
                            self._client_events.extendleft(reversed(events))
                            self._has_client_events.set()
                        return
        except asyncio.CancelledError:
            return

    @staticmethod
    def _write_stdin(stdin: IO[bytes], data: bytes) -> bool:
        try:
            stdin.write(data)
            stdin.flush()
        except (BrokenPipeError, OSError, ValueError):
            return False
        return True

    def _read_stdout(
        self,
        process: ManagedWorkerProcess,
        loop: asyncio.AbstractEventLoop,
        worker_ready: asyncio.Event,
    ) -> None:
        """Reads agent stdout on a thread and hands each streaming event to the event loop until the pipe closes."""
        stdout = process.stdout
        assert stdout is not None
        for line in iter(stdout.readline, b""):
            try:
                event = _STREAMING_EVENT_ADAPTER.validate_json(line)
            except Exception:
                logger.error("Failed to validate agent stdout line: {}", line[:200])
                continue
            loop.call_soon_threadsafe(self._streaming_events.put_nowait, event)
            if isinstance(event, StatusEvent) and event.status_id == "agent_ready":
                loop.call_soon_threadsafe(worker_ready.set)
        logger.info("Agent stdout EOF")

    @staticmethod
    def _read_stderr(process: ManagedWorkerProcess, stderr_lines: list[str]) -> None:
        """Reads stderr from the agent subprocess on a thread, logging each line and collecting it for error reporting."""
        stderr = process.stderr
        assert stderr is not None
        for line in iter(stderr.readline, b""):
            text = line.decode().rstrip()
            if text:
                stderr_lines.append(text)
                logger.warning("agent stderr: {}", text)
