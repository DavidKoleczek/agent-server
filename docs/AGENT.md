# Agent

The `Agent` class is the core AI loop. It can be used standalone without the server in other applications.


## Configuration

Create an `AgentConfig` to configure the agent:

```python
from pathlib import Path
from agent_server.agent.agent import Agent, AgentConfig

config = AgentConfig(
    working_dir=Path("/path/to/project"),
    session_database=Path("conversation.sqlite"),
    max_subagent_depth=1,
)
agent = Agent(config=config)
```

- `working_dir`: Directory the agent operates in.
- `session_database`: Path to the SQLite session database. If omitted, a new database is created under `<working_dir>/.agents/sessions/`.
- `max_subagent_depth`: Maximum recursion depth for sub-agents. `1` means only the main agent can create sub-agents.


## Standalone Usage

The agent communicates through two `asyncio.Queue` instances: a `ClientEvent` queue for incoming client events and a `StreamingEvent` queue for outgoing streaming events. To run the agent in-process:

```python
import asyncio
from pathlib import Path

from agent_server.agent.agent import Agent, AgentConfig
from agent_server.schemas.activity import ClientEvent, StreamingEvent, UserMessageEvent

async def main():
    config = AgentConfig(working_dir=Path("."))
    agent = Agent(config=config)

    user_queue: asyncio.Queue[ClientEvent] = asyncio.Queue()
    agent_queue: asyncio.Queue[StreamingEvent] = asyncio.Queue()

    # Enqueue a message before starting the agent
    user_queue.put_nowait(UserMessageEvent(content="Hello!"))

    # Run the agent and consume its output concurrently
    try:
        async with asyncio.TaskGroup() as tg:
            printer = tg.create_task(print_activities(agent_queue))
            await agent.run(user_queue, agent_queue)
            printer.cancel()
    finally:
        agent.close()

async def print_activities(queue: asyncio.Queue[StreamingEvent]):
    while True:
        event = await queue.get()
        print(event.model_dump(mode="json"))
```

See [scripts/run_agent.py](../scripts/run_agent.py) for a complete working example that feeds timed activities and logs all events to a file.
