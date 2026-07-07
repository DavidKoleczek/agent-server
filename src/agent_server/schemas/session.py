from datetime import datetime
from typing import Literal

from interop_router.types import ChatMessage, SupportedModel
from pydantic import BaseModel, Field

from agent_server.schemas.activity import ActivityState, SessionActivity, TaskPermission


class SessionChatMessage(BaseModel):
    """
    interop-router view
    A Wrapper around ChatMessage that includes additional agent-specific data used by the agent
    """

    position: int
    # Currently this is only set on FunctionCallOutput messages and not depended on (instead we use TaskActivity as the source of truth)
    permission: TaskPermission | None = Field(
        default=None, description="The permission associated with this message, if any."
    )
    agent_id: str = Field(default="main")
    chat_message: ChatMessage


class SessionActivityRecord(BaseModel):
    """
    Client view
    """

    id: str
    position: int
    timestamp: datetime
    type: str
    state: ActivityState
    activity: SessionActivity
    agent_id: str = Field(default="main")


class SessionConfig(BaseModel):
    """Configuration settings for a session.
    Such as model, thinking, tools, etc.
    """

    tool_preset: Literal["permissive", "standard"] = Field(default="permissive", title="Tool Preset")
    model: SupportedModel = Field(default="gpt-5.5", title="Model")
