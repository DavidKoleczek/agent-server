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
        agent_activities: asyncio.Queue[StreamingEvent],
        working_dir: Path,
        session_database: Path | None = None,
    ):
        # A list of user activities that have not been forwarded to the agent yet.
        self._pending: deque[ClientEvent] = deque()
        # This indicates whether there are pending activities that need to be forwarded. This is used to avoid polling on the pending queue.
        self._has_pending = asyncio.Event()
        self._agent_activities = agent_activities
        self._working_dir = working_dir
        self._session_database = session_database
        self._proc: subprocess.Popen[bytes] | None = None
        self._writer_task: asyncio.Task[None] | None = None
        self._stderr_lines: list[str] = []

    async def submit_user_activity(self, activity: UserMessageEvent) -> None:
        """Submits user activities to the agent."""
        self._pending.append(activity)
        self._has_pending.set()

    async def cancel(self) -> None:
        """Immediately kills the agent and any of its sub-agents."""
        proc = self._proc
        if proc is not None and proc.poll() is None:
            proc.kill()
            await asyncio.to_thread(proc.wait)

    async def runner(self) -> None:
        """
        Starts the agent and manages its lifecycle.
        Forwards activities to the agent and writes activities back to the websocket endpoint.
        """
        loop = asyncio.get_running_loop()

        while True:
            # Agent is not running: block until there is work, then spawn a new subprocess.
            # The event is left set so the stdin pump observes it and flushes these initial activities once the process is up.
            await self._has_pending.wait()

            self._stderr_lines = []
            logger.info("Starting agent subprocess")
            await self._agent_activities.put(StatusEvent(status_id="agent_starting"))
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

            # The process closes its pipes on exit, so stdout EOF is the signal that the run has finished.
            stdout_eof = asyncio.Event()
            stdout_thread = threading.Thread(
                target=self._read_stdout, args=(proc, loop, stdout_eof), name="agent-stdout", daemon=True
            )
            stderr_thread = threading.Thread(target=self._read_stderr, args=(proc,), name="agent-stderr", daemon=True)
            stdout_thread.start()
            stderr_thread.start()

            self._writer_task = asyncio.create_task(self._pump_stdin(proc))
            try:
                await stdout_eof.wait()
                # The process is exiting; reaping and joining the reader threads is near-instant now that the pipes have closed.
                # Done in threads so a slow shutdown never blocks the event loop.
                await asyncio.to_thread(proc.wait)
                await asyncio.to_thread(stdout_thread.join)
                await asyncio.to_thread(stderr_thread.join)
            finally:
                self._writer_task.cancel()

                returncode = proc.returncode
                if returncode is not None and returncode != 0:
                    logger.error("Agent subprocess crashed (exit code {})", returncode)
                    detail = (
                        "\n".join(self._stderr_lines)
                        if self._stderr_lines
                        else f"Process exited with code {returncode}"
                    )
                    await self._agent_activities.put(
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

    async def _pump_stdin(self, proc: subprocess.Popen[bytes]) -> None:
        """Forwards pending user activities to the running agent's stdin."""
        stdin = proc.stdin
        assert stdin is not None
        try:
            while True:
                await self._has_pending.wait()
                # Clear before draining so a concurrent append + set during the drain leaves the event set for the next iteration rather than being lost.
                self._has_pending.clear()
                lines: list[str] = []
                while self._pending:
                    activity = self._pending.popleft()
                    lines.append(activity.model_dump_json() + "\n")
                if lines:
                    await asyncio.to_thread(self._write_stdin, stdin, "".join(lines).encode())
        except asyncio.CancelledError:
            return

    @staticmethod
    def _write_stdin(stdin: IO[bytes], data: bytes) -> None:
        try:
            stdin.write(data)
            stdin.flush()
        except (BrokenPipeError, OSError, ValueError):
            # The process exited and closed the pipe; the activities it missed are surfaced by exit handling.
            return

    def _read_stdout(self, proc: subprocess.Popen[bytes], loop: asyncio.AbstractEventLoop, eof: asyncio.Event) -> None:
        """Reads agent stdout on a thread and hands each activity to the event loop until the pipe closes."""
        stdout = proc.stdout
        assert stdout is not None
        try:
            for line in iter(stdout.readline, b""):
                try:
                    activity = _STREAMING_EVENT_ADAPTER.validate_json(line)
                except Exception:
                    logger.error("Failed to validate agent stdout line: {}", line[:200])
                    continue
                loop.call_soon_threadsafe(self._agent_activities.put_nowait, activity)
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
