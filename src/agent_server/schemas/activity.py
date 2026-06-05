from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

ActivityState = Literal["in_progress", "complete", "error", "cancelled"]
TaskPermission = Literal["accepted", "denied", "pending"]

# region Client Events
# Client events are inbound commands from the client. They are not persisted as conversation history.


class UserMessageEvent(BaseModel):
    type: Literal["user_message"] = "user_message"
    content: str


class CancelEvent(BaseModel):
    type: Literal["cancel"] = "cancel"


class QuitEvent(BaseModel):
    type: Literal["quit"] = "quit"


ClientEvent = UserMessageEvent | CancelEvent | QuitEvent

# endregion

# region Session Activities
# Session activities are persisted session history that can be loaded by clients later.


class ActivityBase(BaseModel):
    id: str
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
    permission: TaskPermission = "pending"
    arguments: dict[str, Any] | None = None
    result: str | None = None


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


class ReadyEvent(BaseModel):
    type: Literal["ready"] = "ready"


class TurnStartEvent(BaseModel):
    type: Literal["turn_start"] = "turn_start"


class TurnEndEvent(BaseModel):
    type: Literal["turn_end"] = "turn_end"


class ActivityCreatedEvent(BaseModel):
    type: Literal["activity_created"] = "activity_created"
    activity: SessionActivity


class ActivityDeltaEvent(BaseModel):
    """Used to patch an existing activity. Intended for streaming efficiency."""

    type: Literal["activity_delta"] = "activity_delta"
    activity_id: str
    delta: ActivityDelta


class ActivityUpdatedEvent(BaseModel):
    """The full updated activity."""

    type: Literal["activity_updated"] = "activity_updated"
    activity: SessionActivity


StreamingEvent = (
    ActivityCreatedEvent | ActivityDeltaEvent | ActivityUpdatedEvent | ReadyEvent | TurnStartEvent | TurnEndEvent
)

# endregion
