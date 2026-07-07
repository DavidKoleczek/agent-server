# Agent

The `Agent` class is the core AI loop. It can be used standalone without the server in other applications.


## Configuration

Create an `AgentConfig` to configure the agent:

```python
from pathlib import Path
from agent_server.agent.agent import AgentConfig

config = AgentConfig(
    working_dir=Path("/path/to/project"),
    session_database=Path("conversation.sqlite"),
)
```

- `working_dir`: Directory the agent operates in.
- `session_database`: Path to the SQLite session database. If omitted, a new database is created under `<working_dir>/.agents/sessions/`.


## Standalone Usage

The agent communicates through two `asyncio.Queue` instances passed to its constructor: a `ClientEvent` queue for incoming client events and a `StreamingEvent` queue for outgoing streaming events.
`Agent.start()` is the primary lifecycle entry point. It runs until the host cancels it or the process exits.

```python
import asyncio
from pathlib import Path

from agent_server.agent.agent import Agent, AgentConfig
from agent_server.schemas.activity import ClientEvent, StreamingEvent, UserMessageEvent

async def main():
    client_events: asyncio.Queue[ClientEvent] = asyncio.Queue()
    streaming_events: asyncio.Queue[StreamingEvent] = asyncio.Queue()

    config = AgentConfig(working_dir=Path("."))
    agent = Agent(config=config, client_events=client_events, streaming_events=streaming_events)

    # Enqueue a message before starting the agent
    client_events.put_nowait(UserMessageEvent(content="Hello!"))

    # Run the agent and consume its output concurrently.
    agent_task = asyncio.create_task(agent.start())
    printer_task = asyncio.create_task(print_activities(streaming_events))
    try:
        await agent_task
    finally:
        agent_task.cancel()
        printer_task.cancel()
        await asyncio.gather(agent_task, printer_task, return_exceptions=True)
        agent.close()

async def print_activities(queue: asyncio.Queue[StreamingEvent]):
    while True:
        event = await queue.get()
        print(event.model_dump(mode="json"))
```

See [scripts/run_agent.py](../scripts/run_agent.py) for a complete working example that feeds timed activities and logs all events to a file.


## Sub-agents

Agents constructed with `agent_id="main"` include the `agent` tool. That tool starts a sub-agent in the same working directory and session database, gives it the requested prompt, forwards its streaming events to the caller, and returns the sub-agent's last assistant message as the tool result.

Sub-agents use generated IDs like `sub-1234abcd`. Their persisted activities and chat messages use that ID, while the main agent keeps using `main`. Sub-agents do not receive the `agent` tool.
