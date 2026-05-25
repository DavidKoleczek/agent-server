import dataclasses
import json
from typing import Any, Literal

from interop_router.types import RouterResponse
from openai.types.responses import ResponseStreamEvent
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


class TurnStartActivity(BaseModel):
    type: Literal["turn_start"]


class TurnEndActivity(BaseModel):
    type: Literal["turn_end"]


class ErrorActivity(BaseModel):
    type: Literal["error"]
    error_type: Literal["invalid_client_activity_format", "agent_error"]
    detail: str


class OpenAIStreamActivity(BaseModel):
    type: Literal["openai_stream"]
    model_name: str
    stream_event: ResponseStreamEvent


class RouterResponseActivity(BaseModel):
    type: Literal["router_response"]
    response: RouterResponse


AssistantActivity = (
    ReadyActivity | TurnStartActivity | TurnEndActivity | ErrorActivity | OpenAIStreamActivity | RouterResponseActivity
)

# endregion


def serialize_activity(activity: AssistantActivity) -> dict[str, Any]:
    """Serialize any AssistantActivity to a JSON-compatible dict."""
    if isinstance(activity, BaseModel):
        return activity.model_dump(mode="json", warnings=False)
    if dataclasses.is_dataclass(activity) and not isinstance(activity, type):
        return json.loads(json.dumps(dataclasses.asdict(activity), default=str))
    return {"raw": str(activity)}
