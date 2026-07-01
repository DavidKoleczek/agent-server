import asyncio
from collections import deque
from pathlib import Path
import subprocess
import sys
import threading
from typing import IO
from uuid import uuid4

from loguru import logger
from pydantic import TypeAdapter

from agent_server.schemas.activity import (
    ActivityCreatedEvent,
    ClientEvent,
    ErrorActivity,
    StatusEvent,
    StreamingEvent,
    UserMessageEvent,
)

_STREAMING_EVENT_ADAPTER = TypeAdapter(StreamingEvent)


class AgentManager:
    def __init__(
        self,
        streaming_events: asyncio.Queue[StreamingEvent],
        working_dir: Path,
        session_database: Path | None = None,
    ):
        # A list of user activities that have not been forwarded to the agent yet.
        self._client_events: deque[ClientEvent] = deque()
        # This indicates whether there are pending activities that need to be forwarded. This is used to avoid polling on the pending queue.
        self._has_client_events = asyncio.Event()
        self._streaming_events = streaming_events

        self._working_dir = working_dir
        self._session_database = session_database
        self._proc: subprocess.Popen[bytes] | None = None
        self._writer_task: asyncio.Task[None] | None = None
        self._stderr_lines: list[str] = []
        # We use a flag to indicate that a cancellation was requested so we can differentiate
        # between an issue with the process and a user-initiated cancellation. Similar for shutdown.
        self._cancel_requested = False
        self._shutdown_requested = False

    async def submit_user_event(self, user_event: UserMessageEvent) -> None:
        """Submits user activities to the agent. This is how the websocket endpoint sends user messages to be processed."""
        self._client_events.append(user_event)
        self._has_client_events.set()

    async def cancel(self) -> None:
        """Immediately kills the agent and any of its sub-agents."""
        proc = self._proc
        if proc is not None and proc.poll() is None:
            self._client_events.clear()
            self._has_client_events.clear()
            self._cancel_requested = True
            await self._streaming_events.put(StatusEvent(status_id="agent_cancelling"))
            proc.kill()
            await asyncio.to_thread(proc.wait)

    async def shutdown(self) -> None:
        """Stops the agent subprocess and prevents it from restarting."""
        shutdown_started = not self._shutdown_requested
        self._shutdown_requested = True
        self._client_events.clear()
        self._has_client_events.clear()

        proc = self._proc
        if proc is not None and proc.poll() is None:
            if shutdown_started:
                await self._streaming_events.put(StatusEvent(status_id="agent_stopping"))
            proc.kill()
            await asyncio.to_thread(proc.wait)

    async def start_manager(self) -> None:
        """Starts the agent subprocess and manages its lifecycle. If the agent exits, it will be restarted."""
        while True:
            if self._shutdown_requested:
                break

            self._stderr_lines = []

            logger.info("Starting agent subprocess")
            await self._streaming_events.put(StatusEvent(status_id="agent_starting"))

            # Start the agent_worker process which will start the agent and forward events to it.
            cmd = [
                sys.executable,
                "-m",
                "agent_server.agent.agent_worker",
                "--working-dir",
                str(self._working_dir),
            ]
            if self._session_database is not None:
                cmd.extend(["--session-database", str(self._session_database)])
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self._proc = proc

            loop = asyncio.get_running_loop()
            # We communicate with the agent worker through its stdin and stdout.
            # Start threads to read from stdout and stderr
            stdout_eof = asyncio.Event()
            stdout_thread = threading.Thread(
                target=self._read_stdout, args=(proc, loop, stdout_eof), name="agent-stdout", daemon=True
            )
            stderr_thread = threading.Thread(target=self._read_stderr, args=(proc,), name="agent-stderr", daemon=True)
            stdout_thread.start()
            stderr_thread.start()

            # Start a task to forward pending events to the agent's stdin.
            self._writer_task = asyncio.create_task(self._pump_stdin(proc))

            # Wait until we reach EOF on stdout which indicates the agent has exited. Set by _read_stdout
            # If the agent exits, we will then go back throught the loop starting the worker process again.
            try:
                await self._streaming_events.put(StatusEvent(status_id="agent_ready"))
                await stdout_eof.wait()
                await asyncio.to_thread(proc.wait)
                await asyncio.to_thread(stdout_thread.join)
                await asyncio.to_thread(stderr_thread.join)
            except asyncio.CancelledError:
                self._shutdown_requested = True
                if proc.poll() is None:
                    proc.kill()
                    await asyncio.to_thread(proc.wait)
                    await asyncio.to_thread(stdout_thread.join)
                    await asyncio.to_thread(stderr_thread.join)
                raise
            finally:
                self._writer_task.cancel()

                returncode = proc.returncode
                cancel_requested = self._cancel_requested
                self._cancel_requested = False
                if self._shutdown_requested:
                    logger.info("Agent subprocess exited due to shutdown")
                    await self._streaming_events.put(StatusEvent(status_id="agent_stopped"))
                elif cancel_requested:
                    logger.info("Agent subprocess exited due to cancellation")
                    await self._streaming_events.put(StatusEvent(status_id="agent_cancelled"))
                elif returncode is not None and returncode != 0:
                    logger.error("Agent subprocess crashed (exit code {})", returncode)
                    detail = (
                        "\n".join(self._stderr_lines)
                        if self._stderr_lines
                        else f"Process exited with code {returncode}"
                    )
                    await self._streaming_events.put(
                        ActivityCreatedEvent(
                            activity=ErrorActivity(
                                id=str(uuid4()),
                                state="error",
                                error_type="agent_error",
                                detail=detail,
                            )
                        )
                    )
                else:
                    logger.info("Agent subprocess exited normally")

                self._proc = None
                self._writer_task = None

            if self._shutdown_requested:
                break

    async def _pump_stdin(self, proc: subprocess.Popen[bytes]) -> None:
        """Forwards pending user events to the running agent's stdin."""
        stdin = proc.stdin
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
                        if not self._cancel_requested and not self._shutdown_requested:
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

    def _read_stdout(self, proc: subprocess.Popen[bytes], loop: asyncio.AbstractEventLoop, eof: asyncio.Event) -> None:
        """Reads agent stdout on a thread and hands each streaming event to the event loop until the pipe closes."""
        stdout = proc.stdout
        assert stdout is not None
        try:
            for line in iter(stdout.readline, b""):
                try:
                    event = _STREAMING_EVENT_ADAPTER.validate_json(line)
                except Exception:
                    logger.error("Failed to validate agent stdout line: {}", line[:200])
                    continue
                loop.call_soon_threadsafe(self._streaming_events.put_nowait, event)
        finally:
            logger.info("Agent stdout EOF")
            loop.call_soon_threadsafe(eof.set)

    def _read_stderr(self, proc: subprocess.Popen[bytes]) -> None:
        """Reads stderr from the agent subprocess on a thread, logging each line and collecting it for error reporting."""
        stderr = proc.stderr
        assert stderr is not None
        for line in iter(stderr.readline, b""):
            text = line.decode().rstrip()
            if text:
                self._stderr_lines.append(text)
                logger.warning("agent stderr: {}", text)
