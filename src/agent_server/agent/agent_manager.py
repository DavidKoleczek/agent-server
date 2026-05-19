import asyncio
from collections import deque
import sys

from pydantic import TypeAdapter

from agent_server.schemas.activity import AssistantActivity, UserActivity

_ASSISTANT_ACTIVITY_ADAPTER = TypeAdapter(AssistantActivity)


class AgentManager:
    def __init__(self, agent_activities: asyncio.Queue[AssistantActivity]):
        # A list of user activities that have not been forwarded to the agent yet.
        self._pending: deque[UserActivity] = deque()
        # This indicates whether there are pending activities that need to be forwarded. This is used to avoid polling on the pending queue.
        self._has_pending = asyncio.Event()
        self._agent_activities = agent_activities
        self._proc: asyncio.subprocess.Process | None = None
        self._writer_task: asyncio.Task[None] | None = None
        self._reader_task: asyncio.Task[None] | None = None

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

        # If the agent is already running, we simply forward the new user activities to it.
        # If it is not running, we call `run` to start it.
        # It is up to Agent.run to always block if there are activities to process before returning.
        while True:
            # Block until at least one user activity is pending, without consuming it.
            # The stdin pump (started below) is the sole consumer of self._pending.
            await self._has_pending.wait()

            # Send an acknowledged message here, because while the process is starting their is a slight delay and we might want to figure out a way to keep this warm in the future.

            self._proc = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "agent_server.agent.agent_worker",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
            )
            self._writer_task = asyncio.create_task(self._pump_stdin())
            self._reader_task = asyncio.create_task(self._pump_stdout())
            try:
                await self._proc.wait()
            finally:
                self._writer_task.cancel()
                self._reader_task.cancel()
                self._proc = None
                self._writer_task = None
                self._reader_task = None

    async def _pump_stdin(self) -> None:
        """Continually writes to the stdin that goes to the agent process whenever there are pending user activities."""
        assert self._proc is not None
        assert self._proc.stdin is not None
        stdin = self._proc.stdin
        try:
            while True:
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
                    return
                activity = _ASSISTANT_ACTIVITY_ADAPTER.validate_json(line)
                await self._agent_activities.put(activity)
        except (BrokenPipeError, ConnectionResetError, asyncio.CancelledError):
            return
