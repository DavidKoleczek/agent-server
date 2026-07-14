from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

ActivityState = Literal["in_progress", "complete", "error", "cancelled"]
# Pending means waiting for the user to make a decision, while not determined means the policy
# (determining if we need to auto accept, auto deny, or ask the user) has not finished yet.
TaskPermission = Literal["accepted", "denied", "pending", "not_determined"]

# region Client Events
# Client events are inbound commands from the client. They are not persisted as conversation history.


class UserMessageEvent(BaseModel):
    type: Literal["user_message"] = "user_message"
    content: str


class PermissionChangeEvent(BaseModel):
    type: Literal["permission_change"] = "permission_change"
    agent_id: str = "main"
    id: str
    permission: TaskPermission


class CancelEvent(BaseModel):
    type: Literal["cancel"] = "cancel"


class QuitEvent(BaseModel):
    type: Literal["quit"] = "quit"


class SessionConfigChangeEvent(BaseModel):
    type: Literal["session_config_change"] = "session_config_change"
    config_key: Literal["tool_preset", "model"]
    new_value: str


ClientEvent = UserMessageEvent | PermissionChangeEvent | CancelEvent | QuitEvent | SessionConfigChangeEvent

# endregion

# region Session Activities
# Session activities are persisted session history that can be loaded by clients later.


class ActivityBase(BaseModel):
    id: str
    agent_id: str = Field(default="main")
    state: ActivityState
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class UserActivity(ActivityBase):
    type: Literal["user"] = "user"
    content: str


class AssistantActivity(ActivityBase):
    type: Literal["assistant"] = "assistant"
    content: str


class ReasoningActivity(ActivityBase):
    type: Literal["reasoning"] = "reasoning"
    content: str


class TaskActivity(ActivityBase):
    type: Literal["task"] = "task"
    name: str
    permission: TaskPermission = "not_determined"
    arguments: dict[str, Any] | None = None
    result: str | None = None
    sub_agent_id: str | None = None  # If this Task is a sub-agent, then this will be the ID of the sub-agent


class ErrorActivity(ActivityBase):
    type: Literal["error"] = "error"
    error_type: str
    detail: str


SessionActivity = UserActivity | AssistantActivity | ReasoningActivity | TaskActivity | ErrorActivity

# endregion

# region Streaming Events
# Streaming events are the live updates that the server sends to the client.
# They are ephemeral and not persisted.


class TaskArgumentDelta(BaseModel):
    key: str
    value: Any


class ActivityDelta(BaseModel):
    content_delta: str | None = None
    argument_delta: TaskArgumentDelta | None = None
    result_delta: str | None = None
    permission: TaskPermission | None = None


class StreamingEventBase(BaseModel):
    agent_id: str = Field(default="main")


class ActivityCreatedEvent(StreamingEventBase):
    type: Literal["activity_created"] = "activity_created"
    activity: SessionActivity


class ActivityDeltaEvent(StreamingEventBase):
    """Used to patch an existing activity. Intended for streaming efficiency."""

    type: Literal["activity_delta"] = "activity_delta"
    activity_id: str
    delta: ActivityDelta


class ActivityUpdatedEvent(StreamingEventBase):
    """The full updated activity."""

    type: Literal["activity_updated"] = "activity_updated"
    activity: SessionActivity


class StatusEvent(StreamingEventBase):
    type: Literal["status"] = "status"
    status_id: Literal[
        "agent_starting",
        "agent_ready",
        "agent_cancelling",
        "agent_cancelled",
        "agent_stopping",
        "agent_stopped",
        "agent_running",
        "agent_turn_ended",
        "processing_message",
        "waiting_for_llm_response",
        "processing_llm_response",
        "executing_tool",
        "starting_new_turn",
    ]


class SessionConfigChangedEvent(StreamingEventBase):
    type: Literal["session_config_changed"] = "session_config_changed"
    config_key: str
    new_value: str


StreamingEvent = (
    ActivityCreatedEvent | ActivityDeltaEvent | ActivityUpdatedEvent | StatusEvent | SessionConfigChangedEvent
)

# endregion
