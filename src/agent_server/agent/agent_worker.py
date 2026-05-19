"""
Responsible for spawning the agent process and forwarding and receiving activities from stdin.
"""

import asyncio
import sys

from pydantic import TypeAdapter

from agent_server.agent.agent import Agent
from agent_server.schemas.activity import AssistantActivity, UserActivity

_USER_ACTIVITY_ADAPTER = TypeAdapter(UserActivity)


async def _stdin_reader(queue: asyncio.Queue[UserActivity]) -> None:
    while True:
        line = await asyncio.to_thread(sys.stdin.buffer.readline)
        if not line:
            return
        activity = _USER_ACTIVITY_ADAPTER.validate_json(line)
        await queue.put(activity)


async def _stdout_writer(queue: asyncio.Queue[AssistantActivity]) -> None:
    def _write(payload: bytes) -> None:
        sys.stdout.buffer.write(payload)
        sys.stdout.buffer.flush()

    while True:
        activity = await queue.get()
        line = activity.model_dump_json() + "\n"
        await asyncio.to_thread(_write, line.encode())


async def main() -> None:
    user_queue: asyncio.Queue[UserActivity] = asyncio.Queue()
    agent_queue: asyncio.Queue[AssistantActivity] = asyncio.Queue()

    reader_task = asyncio.create_task(_stdin_reader(user_queue))
    writer_task = asyncio.create_task(_stdout_writer(agent_queue))

    try:
        await Agent().run(user_queue, agent_queue)
    finally:
        reader_task.cancel()
        writer_task.cancel()


if __name__ == "__main__":
    asyncio.run(main())
