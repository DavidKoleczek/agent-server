"""
Run the Agent standalone.

ACTIVITIES defines a sequence of user activities to feed into the agent after that many seconds from the start.
"""

import asyncio
import contextlib
from datetime import datetime
import json
from pathlib import Path
import tempfile

from agent_server.agent.agent import Agent, AgentConfig
from agent_server.schemas.activity import AssistantActivity, UserActivity, serialize_activity

REPO_ROOT = Path(__file__).parents[1]
EVENTS_DIR = REPO_ROOT / "temp"

WORKING_DIR: Path | None = None

# (content, delay_seconds_from_start)
ACTIVITIES: list[tuple[str, float]] = [
    ("Can you write a haiku about bears and put in bear_haiku.md?", 0),
    ("Can you also write one about ducks in put it in duck_haiku.md", 5),
]


async def feed_activities(queue: asyncio.Queue[UserActivity], start_time: float, log: Path) -> None:
    for content, offset in ACTIVITIES:
        if offset <= 0:
            continue
        now = asyncio.get_event_loop().time()
        wait = offset - (now - start_time)
        if wait > 0:
            await asyncio.sleep(wait)
        activity = UserActivity(type="user_message", content=content)
        _append_event(log, {"source": "client", "activity": activity.model_dump()})
        queue.put_nowait(activity)


async def log_activities(queue: asyncio.Queue[AssistantActivity], log: Path) -> None:
    while True:
        activity = await queue.get()
        _append_event(log, {"source": "assistant", "activity": serialize_activity(activity)})


def _append_event(log: Path, event: dict[str, object]) -> None:
    with log.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, indent=2) + "\n")


async def main() -> None:
    user_q: asyncio.Queue[UserActivity] = asyncio.Queue()
    agent_q: asyncio.Queue[AssistantActivity] = asyncio.Queue()

    EVENTS_DIR.mkdir(parents=True, exist_ok=True)
    log = EVENTS_DIR / f"run_agent_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    print(f"events log: {log}")

    if WORKING_DIR is not None:
        WORKING_DIR.mkdir(parents=True, exist_ok=True)
        tmp_ctx: contextlib.AbstractContextManager[str] = contextlib.nullcontext(str(WORKING_DIR))
    else:
        tmp_ctx = tempfile.TemporaryDirectory(prefix="agent-server-")

    with tmp_ctx as working_dir_str:
        working_dir = Path(working_dir_str)
        print(f"working_dir: {working_dir}")
        config = AgentConfig(working_dir=working_dir)
        agent = Agent(config=config)

        # Enqueue all immediate activities (offset=0) before starting the agent so the drain doesn't race against them.
        start_time = asyncio.get_event_loop().time()
        for content, offset in ACTIVITIES:
            if offset > 0:
                break
            activity = UserActivity(type="user_message", content=content)
            _append_event(log, {"source": "client", "activity": activity.model_dump()})
            user_q.put_nowait(activity)

        async with asyncio.TaskGroup() as tg:
            tg.create_task(agent.run(user_q, agent_q))
            tg.create_task(log_activities(agent_q, log))
            tg.create_task(feed_activities(user_q, start_time, log))

    print(f"done. events written to: {log}")


if __name__ == "__main__":
    asyncio.run(main())
