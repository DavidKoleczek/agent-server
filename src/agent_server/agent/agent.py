"""
The agent needs to be initialized such that it can call itself in a sub-task.
It needs to be able to recieve messages mid-task and inject those into the history so they get picked up the next iteration of the agent loop (they don't get added to sub-agents).
"""

from interop_router.types import ChatMessage


class Agent:
    def __init__(self):
        self.history: list[ChatMessage] = []
