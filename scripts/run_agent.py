"""
Run the Agent standalone.

ACTIVITIES defines a sequence of user activities to feed into the agent after that many seconds from the start.

Writes:
- Events log: temp/run_agent_<timestamp>.jsonl at the repo root. Each client and assistant event is logged.
- Working directory: WORKING_DIR if set, otherwise a temporary directory (prefixed "agent-server-") that is removed on exit. The agent reads and writes files here as instructed.
- Session database: the agent persists chat history and activities to a SQLite db at <working_dir>/.agents/sessions/<working-dir-name>_<date>_<short-uuid>.sqlite.
It lives inside the working directory, so it is ephemeral when the working directory is the temporary one.
- stdout: Only basic stuff: events log path and working dir at startup, completion line at the end.
"""

import asyncio
import contextlib
from datetime import datetime
import json
from pathlib import Path
import tempfile

from agent_server.agent.agent import Agent, AgentConfig
from agent_server.schemas.activity import ClientEvent, StreamingEvent, UserMessageEvent

REPO_ROOT = Path(__file__).parents[1]
EVENTS_DIR = REPO_ROOT / "temp"

WORKING_DIR: Path | None = REPO_ROOT

# (content, delay_seconds_from_start)
# Swap these around when needed


ACTIVITIES: list[tuple[str, float]] = [
    ("Hello!", 0),
]

ACTIVITIES: list[tuple[str, float]] = [
    ("Can you tell me what the files are in this dir?", 0),
]

ACTIVITIES: list[tuple[str, float]] = [
    ("Can you write a haiku about bears and put in bear_haiku.md?", 0),
    ("Can you also write one about ducks in put it in duck_haiku.md", 8),
]


async def feed_activities(queue: asyncio.Queue[ClientEvent], start_time: float, log: Path) -> None:
    for content, offset in ACTIVITIES:
        if offset <= 0:
            continue
        now = asyncio.get_event_loop().time()
        wait = offset - (now - start_time)
        if wait > 0:
            await asyncio.sleep(wait)
        activity = UserMessageEvent(content=content)
        _append_event(log, {"source": "client", "activity": activity.model_dump(mode="json")})
        queue.put_nowait(activity)


async def log_activities(queue: asyncio.Queue[StreamingEvent], log: Path) -> None:
    while True:
        activity = await queue.get()
        try:
            _append_event(log, {"source": "assistant", "activity": activity.model_dump(mode="json")})
        finally:
            queue.task_done()


def _append_event(log: Path, event: dict[str, object]) -> None:
    with log.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event) + "\n")


async def main() -> None:
    user_q: asyncio.Queue[ClientEvent] = asyncio.Queue()
    agent_q: asyncio.Queue[StreamingEvent] = asyncio.Queue()

    EVENTS_DIR.mkdir(parents=True, exist_ok=True)
    log = EVENTS_DIR / f"run_agent_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
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
        try:
            # Enqueue all immediate activities (offset=0) before starting the agent so the drain doesn't race against them.
            start_time = asyncio.get_event_loop().time()
            for content, offset in ACTIVITIES:
                if offset > 0:
                    break
                activity = UserMessageEvent(content=content)
                _append_event(log, {"source": "client", "activity": activity.model_dump(mode="json")})
                user_q.put_nowait(activity)

            async with asyncio.TaskGroup() as tg:
                logger_task = tg.create_task(log_activities(agent_q, log))
                feed_task = tg.create_task(feed_activities(user_q, start_time, log))
                await agent.run(user_q, agent_q)
                await feed_task
                await agent_q.join()
                logger_task.cancel()
        finally:
            agent.close()

    print(f"done. events written to: {log}")


if __name__ == "__main__":
    asyncio.run(main())
