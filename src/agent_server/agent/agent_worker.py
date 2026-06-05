"""
Responsible for spawning the agent process and forwarding and receiving activities from stdin.
"""

import argparse
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

from loguru import logger
from pydantic import TypeAdapter

from agent_server.agent.agent import Agent, AgentConfig
from agent_server.schemas.activity import ClientEvent, StreamingEvent

_USER_ACTIVITY_ADAPTER = TypeAdapter(ClientEvent)


async def main() -> None:
    _patch_subprocess_default_stdin()

    parser = argparse.ArgumentParser()
    parser.add_argument("--working-dir", type=Path, required=True)
    parser.add_argument("--session-database", type=Path, default=None)
    args = parser.parse_args()

    user_queue: asyncio.Queue[ClientEvent] = asyncio.Queue()
    agent_queue: asyncio.Queue[StreamingEvent] = asyncio.Queue()

    logger.info("Agent worker starting (working_dir={})", args.working_dir)
    config = AgentConfig(working_dir=args.working_dir, session_database=args.session_database)
    agent = Agent(config=config)
    logger.info("Agent initialized")

    reader_task = asyncio.create_task(_stdin_reader(user_queue))
    writer_task = asyncio.create_task(_stdout_writer(agent_queue))
    try:
        while True:
            # The reader thread continuously drains stdin into user_queue, so input is accepted at all times.
            # Here we only block until there is work to begin a turn; agent.run then drains the queue itself,
            # including any steering messages that arrive mid-turn.
            first = await user_queue.get()
            user_queue.put_nowait(first)
            try:
                await agent.run(user_queue, agent_queue)
            except Exception:
                logger.exception("agent.run raised an exception")
                raise
    finally:
        reader_task.cancel()
        writer_task.cancel()
        exit_code = 1 if sys.exc_info()[1] is not None else 0
        try:
            # Drain any remaining activities so the parent sees them before EOF.
            while not agent_queue.empty():
                activity = agent_queue.get_nowait()
                line = json.dumps(activity.model_dump(mode="json")) + "\n"
                sys.stdout.buffer.write(line.encode())
            sys.stdout.buffer.flush()
        except Exception:
            pass
        # The _stdin_reader thread blocks on readline() from the piped stdin
        # and cannot be interrupted. Force exit so the non-daemon thread
        # doesn't prevent shutdown.
        os._exit(exit_code)


def _patch_subprocess_default_stdin() -> None:
    """Prevent child processes from inheriting this process's stdin pipe.

    On Windows, when a thread blocks on readline() from a piped stdin,
    any subprocess.run/Popen call that inherits the same pipe handle will deadlock.
    This worker reads stdin in a background thread, so we patch Popen to default to stdin=DEVNULL.
    Callers that explicitly pass stdin= are unaffected.
    """
    _original_init = subprocess.Popen.__init__

    def _patched_init(self: Any, *args: Any, **kwargs: Any) -> None:
        if "stdin" not in kwargs:
            kwargs["stdin"] = subprocess.DEVNULL
        _original_init(self, *args, **kwargs)

    type.__setattr__(subprocess.Popen, "__init__", _patched_init)


async def _stdout_writer(queue: asyncio.Queue[StreamingEvent]) -> None:
    def _write(payload: bytes) -> None:
        sys.stdout.buffer.write(payload)
        sys.stdout.buffer.flush()

    while True:
        activity = await queue.get()
        line = json.dumps(activity.model_dump(mode="json")) + "\n"
        await asyncio.to_thread(_write, line.encode())


async def _stdin_reader(queue: asyncio.Queue[ClientEvent]) -> None:
    while True:
        line = await asyncio.to_thread(sys.stdin.buffer.readline)
        if not line:
            return
        activity = _USER_ACTIVITY_ADAPTER.validate_json(line)
        await queue.put(activity)


if __name__ == "__main__":
    asyncio.run(main())
