from typing import Any, Literal

from openai.types.responses import (
    ResponseCompletedEvent,
    ResponseCreatedEvent,
    ResponseFunctionCallArgumentsDeltaEvent,
    ResponseReasoningSummaryTextDeltaEvent,
    ResponseTextDeltaEvent,
)
from pydantic import BaseModel

# region: Client
# These are expected to be sent by the client at any time.


class UserActivity(BaseModel):
    type: Literal["user_message"]
    content: str


class CancelActivity(BaseModel):
    type: Literal["cancel"]


class QuitActivity(BaseModel):
    type: Literal["quit"]


# endregion

ClientActivity = UserActivity | CancelActivity | QuitActivity

# region: Agent


class ReadyActivity(BaseModel):
    type: Literal["ready"]


class ToolActivity(BaseModel):
    type: Literal["tool"]
    tool_name: str
    tool_arguments: dict[str, Any]
    tool_output: str


class ErrorActivity(BaseModel):
    type: Literal["error"]
    error_type: Literal["invalid_client_activity_format", "agent_error"]
    detail: str


AssistantActivity = (
    ResponseCreatedEvent
    | ResponseFunctionCallArgumentsDeltaEvent
    | ResponseCompletedEvent
    | ResponseTextDeltaEvent
    | ResponseReasoningSummaryTextDeltaEvent
    | ReadyActivity
    | ToolActivity
    | ErrorActivity
)

# endregion
