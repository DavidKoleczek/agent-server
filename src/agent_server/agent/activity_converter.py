"""NOTE: AI Generated - Update Later
Converts a RouterResponse into SessionActivities.
This is not meant for streaming, but for clients to use for displaying the activity history.
"""

from collections.abc import Iterable, Mapping
import json
from typing import Any, cast

from interop_router.types import ChatMessage, RouterResponse

from agent_server.schemas.activity import (
    AssistantActivity,
    ErrorActivity,
    ReasoningActivity,
    SessionActivity,
    TaskActivity,
    UserActivity,
)


def response_to_activities(response: RouterResponse) -> list[SessionActivity]:
    """Convert a RouterResponse into the SessionActivities used to display activity history."""
    activities: list[SessionActivity] = []
    for message in response.output:
        activity = _response_item_to_activity(message)
        if activity is None:
            continue
        activities.append(activity)
    return activities


def _response_item_to_activity(message: ChatMessage) -> SessionActivity | None:
    item = message.message
    if not isinstance(item, Mapping):
        raise TypeError("Response item message must be a JSON object.")

    item_type = item.get("type")
    role = item.get("role")
    if item_type == "message" or role == "user" or role == "assistant":
        return _message_item_to_activity(message, item)
    if item_type == "reasoning":
        return _reasoning_item_to_activity(message, item)
    if item_type == "function_call":
        return _function_call_item_to_activity(message, item)
    if item_type == "function_call_output":
        return None

    return None


def _message_item_to_activity(message: ChatMessage, item: Mapping[str, Any]) -> SessionActivity | None:
    role = item.get("role")
    content = item.get("content")
    match role:
        case "user":
            return UserActivity(
                id=message.id,
                state="complete",
                timestamp=message.timestamp,
                content=_text_from_content(content, text_type="input_text", text_key="text"),
            )
        case "assistant":
            refusal = _text_from_content(content, text_type="refusal", text_key="refusal")
            if refusal:
                return ErrorActivity(
                    id=message.id,
                    state="error",
                    timestamp=message.timestamp,
                    error_type="assistant_refusal",
                    detail=refusal,
                )

            return AssistantActivity(
                id=message.id,
                state="complete",
                timestamp=message.timestamp,
                content=_text_from_content(content, text_type="output_text", text_key="text"),
            )
        case _:
            return None


def _reasoning_item_to_activity(message: ChatMessage, item: Mapping[str, Any]) -> ReasoningActivity | None:
    content = _text_from_content(item.get("summary"), text_type="summary_text", text_key="text")
    if not content:
        content = _text_from_content(item.get("content"), text_type="reasoning_text", text_key="text")
    if not content:
        return None

    return ReasoningActivity(
        id=message.id,
        state="complete",
        timestamp=message.timestamp,
        content=content,
    )


def _function_call_item_to_activity(message: ChatMessage, item: Mapping[str, Any]) -> TaskActivity:
    name = item.get("name")
    if not isinstance(name, str):
        raise TypeError("Function call response item must include a string name.")

    raw_arguments = item.get("arguments")
    if not isinstance(raw_arguments, str):
        raise TypeError("Function call response item must include JSON string arguments.")

    try:
        arguments = json.loads(raw_arguments)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Function call arguments for {name} must be valid JSON.") from exc
    if not isinstance(arguments, dict):
        raise TypeError(f"Function call arguments for {name} must be a JSON object.")

    return TaskActivity(
        id=message.id,
        state="complete",
        timestamp=message.timestamp,
        name=name,
        permission="accepted",
        arguments=arguments,
    )


def _text_from_content(content: object, *, text_type: str, text_key: str) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, Iterable) or isinstance(content, Mapping):
        return ""

    text_items: list[str] = []
    for item in content:
        if not isinstance(item, Mapping):
            continue

        content_item = cast(Mapping[str, Any], item)
        if content_item.get("type") != text_type:
            continue

        text = content_item.get(text_key)
        if isinstance(text, str):
            text_items.append(text)

    return "\n".join(text_items)
