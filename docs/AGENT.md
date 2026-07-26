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
    default_model="gpt-5.6-sol",
)
```

- `working_dir` (required): Directory the agent operates in.
- `session_database` (optional): Path to the SQLite session database. If omitted, a new database is created under `<working_dir>/.agents/sessions/`.
- `default_model` (optional): Initial model for sessions without stored configuration. Invalid model names use the server default, and existing session configuration takes precedence.


## Standalone Usage

The agent communicates through two `asyncio.Queue` instances passed to its constructor: a `ClientEvent` queue for incoming client events and a `StreamingEvent` queue for outgoing streaming events.
`Agent.start()` is the primary lifecycle entry point. It runs until the host cancels it or the process exits.

```python
import asyncio
from pathlib import Path

from agent_server.agent.agent import Agent, AgentConfig
from agent_server.schemas.activity import ClientEvent, StreamingEvent, UserMessageEvent


async def print_activities(queue: asyncio.Queue[StreamingEvent]) -> None:
    while True:
        event = await queue.get()
        print(event.model_dump(mode="json"))


async def main() -> None:
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


if __name__ == "__main__":
    asyncio.run(main())
```

See [scripts/run_agent.py](../scripts/run_agent.py) for a complete working example that feeds timed activities and logs all events to a file.


## Modes and Input Requests

The main agent supports `default` and `plan` modes. Send a `ModeChangeEvent` through the client event queue to change modes. 
The selected mode is persisted in the session database. 
Changing modes updates the available tools and stores a passive system reminder for the next model request without starting one.
Requesting the active mode is a no-op.
Plan mode instructs the agent to create a plan under `<working_dir>/.agents/plans/` and submit it for approval before implementation.

The main agent can emit an `InputRequestActivity` when it needs answers or plan approval. 
The host must present every item and return an `InputRequestResponseEvent` with the activity ID, item IDs, and selected option IDs or free-form responses. 
The completed activity persists the selections and the agent resumes processing. 
See [Agent WebSocket](AGENT_WEBSOCKET.md) for the event and activity schemas.


## Sub-agents

Agents constructed with `agent_id="main"` include the `agent` tool. 
That tool starts a sub-agent in the same working directory and session database, gives it the requested prompt, 
forwards its streaming events to the caller, and returns the sub-agent's last assistant message as the tool result.
Sub-agents use generated IDs like `sub-1234abcd`. 
Their persisted activities and chat messages use that ID, while the main agent keeps using `main`. 
Sub-agents do not receive the `agent`, `ask_user`, or `propose_plan` tools.
Each sub-agent runs through its own `AgentManager`. 
When the agent is hosted by the server, sub-agent workers remain inside the main worker's managed process tree so cancellation and shutdown cannot orphan them.
