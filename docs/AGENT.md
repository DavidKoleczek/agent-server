# Agent

The `Agent` class is the core AI loop. It can be used standalone without the server in other applications.


## Configuration

Create an `AgentConfig` to configure the agent:

```python
from pathlib import Path
from agent_server.agent.agent import Agent, AgentConfig

config = AgentConfig(
    working_dir=Path("/path/to/project"),
    chat_file=Path("conversation.json"),
    max_subagent_depth=1,
)
agent = Agent(config=config)
```


## Standalone Usage

The agent communicates through two `asyncio.Queue` instances: one for incoming user activities and one for outgoing assistant activities. To run the agent in-process:

```python
import asyncio
from agent_server.agent.agent import Agent, AgentConfig
from agent_server.schemas.activity import AssistantActivity, UserActivity, serialize_activity

async def main():
    config = AgentConfig(working_dir=Path("."))
    agent = Agent(config=config)

    user_queue: asyncio.Queue[UserActivity] = asyncio.Queue()
    agent_queue: asyncio.Queue[AssistantActivity] = asyncio.Queue()

    # Enqueue a message before starting the agent
    user_queue.put_nowait(UserActivity(type="user_message", content="Hello!"))

    # Run the agent and consume its output concurrently
    async with asyncio.TaskGroup() as tg:
        tg.create_task(agent.run(user_queue, agent_queue))
        tg.create_task(print_activities(agent_queue))

async def print_activities(queue: asyncio.Queue[AssistantActivity]):
    while True:
        activity = await queue.get()
        print(serialize_activity(activity))
```

See [scripts/run_agent.py](../scripts/run_agent.py) for a complete working example that feeds timed activities and logs all events to a file.

