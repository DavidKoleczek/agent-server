from datetime import datetime

from interop_router.types import ChatMessage
from pydantic import BaseModel, Field

from agent_server.schemas.activity import ActivityState, SessionActivity, TaskPermission


class SessionChatMessage(BaseModel):
    """
    Wrapper around interop-router's ChatMessage that includes additional agent-specific data.
    """

    position: int
    permission: TaskPermission | None = Field(
        default=None, description="The permission associated with this message, if any."
    )
    chat_message: ChatMessage


class SessionActivityRecord(BaseModel):
    """
    Client facing list of activities.
    """

    id: str
    position: int
    timestamp: datetime
    type: str
    state: ActivityState
    activity: SessionActivity
