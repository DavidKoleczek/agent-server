"""
The agent needs to be initialized such that it can call itself in a sub-task.
It needs to be able to recieve messages mid-task and inject those into the history so they get picked up the next iteration of the agent loop (they don't get added to sub-agents).
"""

import asyncio

from interop_router.types import ChatMessage
from openai.types.responses import ResponseTextDeltaEvent

from agent_server.schemas.activity import AssistantActivity, UserActivity


class Agent:
    def __init__(self):
        self.history: list[ChatMessage] = []

    async def run(
        self, user_activities: asyncio.Queue[UserActivity], agent_activities: asyncio.Queue[AssistantActivity]
    ) -> None:
        """
        Handles all the AI agent logic. It interacts with the outside world by ready and writing to user_activities and agent_activities, respectively.
        """
        # Temporary echo behavior for end-to-end testing: stream each character of the user's content back as a ResponseTextDeltaEvent.
        sequence_number = 0
        while True:
            user_activity = await user_activities.get()
            for character in user_activity.content:
                await agent_activities.put(
                    ResponseTextDeltaEvent(
                        type="response.output_text.delta",
                        content_index=0,
                        delta=character,
                        item_id="echo",
                        logprobs=[],
                        output_index=0,
                        sequence_number=sequence_number,
                    )
                )
                sequence_number += 1
