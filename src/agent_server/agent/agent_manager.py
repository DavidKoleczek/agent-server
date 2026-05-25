import asyncio
from collections import deque
from pathlib import Path
import sys

from loguru import logger
from pydantic import TypeAdapter

from agent_server.schemas.activity import AssistantActivity, ErrorActivity, UserActivity

_ASSISTANT_ACTIVITY_ADAPTER = TypeAdapter(AssistantActivity)


class AgentManager:
    def __init__(
        self,
        agent_activities: asyncio.Queue[AssistantActivity],
        working_dir: Path,
        chat_file: Path | None = None,
    ):
        # A list of user activities that have not been forwarded to the agent yet.
        self._pending: deque[UserActivity] = deque()
        # This indicates whether there are pending activities that need to be forwarded. This is used to avoid polling on the pending queue.
        self._has_pending = asyncio.Event()
        self._agent_activities = agent_activities
        self._working_dir = working_dir
        self._chat_file = chat_file
        self._proc: asyncio.subprocess.Process | None = None
        self._writer_task: asyncio.Task[None] | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_lines: list[str] = []

    async def submit_user_activity(self, activity: UserActivity) -> None:
        """Submits user activities to the agent."""
        self._pending.append(activity)
        self._has_pending.set()

    async def cancel(self) -> None:
        """Immediately kills the agent and any of its sub-agents."""
        proc = self._proc
        if proc is not None and proc.returncode is None:
            proc.kill()
            await proc.wait()

    async def runner(self):
        """
        Starts the agent and manages its lifecycle.
        Forwards activities to the agent and writes activities back to the websocket endpoint.
        """

        while True:
            # Agent is not running: block until there is work, then spawn a new subprocess.
            await self._has_pending.wait()
            self._has_pending.clear()

            self._stderr_lines = []
            logger.info("Starting agent subprocess")
            cmd = [
                sys.executable,
                "-m",
                "agent_server.agent.agent_worker",
                "--working-dir",
                str(self._working_dir),
            ]
            if self._chat_file is not None:
                cmd.extend(["--chat-file", str(self._chat_file)])
            self._proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            # Flush initial activities directly before starting the pump task
            assert self._proc.stdin is not None
            while self._pending:
                activity = self._pending.popleft()
                line = activity.model_dump_json() + "\n"
                self._proc.stdin.write(line.encode())
            await self._proc.stdin.drain()

            self._writer_task = asyncio.create_task(self._pump_stdin())
            self._reader_task = asyncio.create_task(self._pump_stdout())
            stderr_task = asyncio.create_task(self._pump_stderr())
            try:
                await self._proc.wait()
                # Let the pump tasks drain remaining output after process exit.
                await stderr_task
                await self._reader_task
            finally:
                self._writer_task.cancel()
                self._reader_task.cancel()
                stderr_task.cancel()

                returncode = self._proc.returncode
                if returncode is not None and returncode != 0:
                    logger.error("Agent subprocess crashed (exit code {})", returncode)
                    detail = (
                        "\n".join(self._stderr_lines)
                        if self._stderr_lines
                        else f"Process exited with code {returncode}"
                    )
                    await self._agent_activities.put(
                        ErrorActivity(type="error", error_type="agent_error", detail=detail)
                    )
                else:
                    logger.info("Agent subprocess exited normally")

                self._proc = None
                self._writer_task = None
                self._reader_task = None

    async def _pump_stdin(self) -> None:
        """Forwards pending user activities to the running agent's stdin."""
        assert self._proc is not None
        assert self._proc.stdin is not None
        stdin = self._proc.stdin
        try:
            while True:
                # Agent is already running : wait for new activities and forward them.
                await self._has_pending.wait()
                # Clear before draining so a concurrent append + set during the drain leaves the event set for the next iteration rather than being lost.
                self._has_pending.clear()
                while self._pending:
                    activity = self._pending.popleft()
                    line = activity.model_dump_json() + "\n"
                    stdin.write(line.encode())
                await stdin.drain()
        except (BrokenPipeError, ConnectionResetError, asyncio.CancelledError):
            return

    async def _pump_stdout(self) -> None:
        """Continually reads from the stdout of the agent process and puts the activities into the agent_activities queue."""
        assert self._proc is not None
        assert self._proc.stdout is not None
        stdout = self._proc.stdout
        try:
            while True:
                line = await stdout.readline()
                if not line:
                    logger.info("Agent stdout EOF")
                    return
                try:
                    activity = _ASSISTANT_ACTIVITY_ADAPTER.validate_json(line)
                except Exception:
                    logger.error("Failed to validate agent stdout line: {}", line[:200])
                    continue
                logger.debug("Forwarding activity: {}", type(activity).__name__)
                await self._agent_activities.put(activity)
        except (BrokenPipeError, ConnectionResetError, asyncio.CancelledError):
            return

    async def _pump_stderr(self) -> None:
        """Reads stderr from the agent subprocess, logs each line, and collects them for error reporting."""
        assert self._proc is not None
        assert self._proc.stderr is not None
        stderr = self._proc.stderr
        try:
            while True:
                line = await stderr.readline()
                if not line:
                    return
                text = line.decode().rstrip()
                if text:
                    self._stderr_lines.append(text)
                    logger.warning("agent stderr: {}", text)
        except (BrokenPipeError, ConnectionResetError, asyncio.CancelledError):
            return
