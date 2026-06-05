"""NOTE: AI Generated - Update Later
Converts a live OpenAI response stream into ephemeral client StreamingEvents.
"""

from dataclasses import dataclass, field
from typing import Any
import uuid

import jiter
from openai.types.responses import (
    ResponseErrorEvent,
    ResponseFailedEvent,
    ResponseFunctionCallArgumentsDeltaEvent,
    ResponseFunctionToolCall,
    ResponseIncompleteEvent,
    ResponseOutputItemAddedEvent,
    ResponseOutputItemDoneEvent,
    ResponseOutputMessage,
    ResponseOutputRefusal,
    ResponseOutputText,
    ResponseReasoningItem,
    ResponseReasoningSummaryPartAddedEvent,
    ResponseReasoningSummaryTextDeltaEvent,
    ResponseRefusalDeltaEvent,
    ResponseStreamEvent,
    ResponseTextDeltaEvent,
)

from agent_server.schemas.activity import (
    ActivityCreatedEvent,
    ActivityDelta,
    ActivityDeltaEvent,
    ActivityUpdatedEvent,
    AssistantActivity,
    ErrorActivity,
    ReasoningActivity,
    SessionActivity,
    StreamingEvent,
    TaskActivity,
    TaskArgumentDelta,
)


@dataclass
class _StreamItem:
    """Mutable per-output-index state accumulated while a single output item streams.

    Finalization reads the authoritative final item from the `output_item.done` event, so only state
    that cannot be recovered there is tracked: lazy-creation status, reasoning parts (for the create
    snapshot and the separator), and the function-call argument buffer plus the last emitted snapshot.
    """

    activity_id: str
    # Whether an ActivityCreatedEvent has been emitted. Reasoning items create lazily on first content.
    created: bool = False
    summary_parts: dict[int, str] = field(default_factory=dict)
    raw_arguments: str = ""
    emitted_arguments: dict[str, Any] = field(default_factory=dict)


class ActivityStreamConverter:
    """Converts OpenAI ResponseStreamEvents into ephemeral client StreamingEvents.

    Stateful: a single instance must be used for exactly one response stream, since it tracks the
    in-progress activity for each output index across delta events.
    """

    def __init__(self) -> None:
        self._items: dict[int, _StreamItem] = {}

    def handle(self, event: ResponseStreamEvent) -> list[StreamingEvent]:
        if isinstance(event, ResponseOutputItemAddedEvent):
            return self._handle_item_added(event)
        if isinstance(event, ResponseOutputItemDoneEvent):
            return self._handle_item_done(event)
        if isinstance(event, ResponseErrorEvent | ResponseFailedEvent | ResponseIncompleteEvent):
            return [ActivityCreatedEvent(activity=_error_activity(event))]

        # The remaining handled events are deltas scoped to an in-progress item, looked up by output index.
        record = self._delta_record(event)
        if record is None:
            return []
        if isinstance(event, ResponseTextDeltaEvent | ResponseRefusalDeltaEvent):
            # Assistant text and refusal both stream as content; a refusal becomes an ErrorActivity on done.
            return [ActivityDeltaEvent(activity_id=record.activity_id, delta=ActivityDelta(content_delta=event.delta))]
        if isinstance(event, ResponseReasoningSummaryPartAddedEvent):
            record.summary_parts[event.summary_index] = event.part.text
            # A new summary part begins; parts are newline-joined, so the separator keeps the streamed
            # content equal to the finalized content (the lazy-create path snapshots full content instead).
            return self._emit_reasoning(record, "\n" + event.part.text)
        if isinstance(event, ResponseReasoningSummaryTextDeltaEvent):
            record.summary_parts[event.summary_index] = record.summary_parts.get(event.summary_index, "") + event.delta
            return self._emit_reasoning(record, event.delta)
        if isinstance(event, ResponseFunctionCallArgumentsDeltaEvent):
            return self._handle_arguments_delta(record, event)
        return []

    def _delta_record(self, event: ResponseStreamEvent) -> _StreamItem | None:
        """Resolve the in-progress item a delta event targets, or None if there is no active item."""
        output_index = getattr(event, "output_index", None)
        if not isinstance(output_index, int):
            return None
        return self._items.get(output_index)

    def _handle_item_added(self, event: ResponseOutputItemAddedEvent) -> list[StreamingEvent]:
        item = event.item
        if isinstance(item, ResponseOutputMessage):
            self._items[event.output_index] = _StreamItem(activity_id=item.id, created=True)
            return [ActivityCreatedEvent(activity=AssistantActivity(id=item.id, state="in_progress", content=""))]
        if isinstance(item, ResponseFunctionToolCall):
            activity_id = item.id or item.call_id
            self._items[event.output_index] = _StreamItem(activity_id=activity_id, created=True)
            return [
                ActivityCreatedEvent(
                    activity=TaskActivity(id=activity_id, state="in_progress", name=item.name, permission="pending")
                )
            ]
        if isinstance(item, ResponseReasoningItem):
            # Created lazily on the first summary content so a reasoning item without a summary
            # produces no activity, matching the non-streaming converter.
            self._items[event.output_index] = _StreamItem(activity_id=item.id)
        return []

    def _emit_reasoning(self, record: _StreamItem, content_delta: str) -> list[StreamingEvent]:
        """Create the reasoning activity on first content, then patch it with deltas thereafter."""
        if not record.created:
            record.created = True
            activity = ReasoningActivity(id=record.activity_id, state="in_progress", content=_reasoning_content(record))
            return [ActivityCreatedEvent(activity=activity)]
        return [ActivityDeltaEvent(activity_id=record.activity_id, delta=ActivityDelta(content_delta=content_delta))]

    def _handle_arguments_delta(
        self, record: _StreamItem, event: ResponseFunctionCallArgumentsDeltaEvent
    ) -> list[StreamingEvent]:
        record.raw_arguments += event.delta
        parsed = _parse_partial_arguments(record.raw_arguments)

        events: list[StreamingEvent] = []
        for key, value in parsed.items():
            if key not in record.emitted_arguments or record.emitted_arguments[key] != value:
                record.emitted_arguments[key] = value
                events.append(
                    ActivityDeltaEvent(
                        activity_id=record.activity_id,
                        delta=ActivityDelta(argument_delta=TaskArgumentDelta(key=key, value=value)),
                    )
                )
        return events

    def _handle_item_done(self, event: ResponseOutputItemDoneEvent) -> list[StreamingEvent]:
        record = self._items.pop(event.output_index, None)
        if record is None:
            return []
        item = event.item
        if isinstance(item, ResponseOutputMessage):
            return [ActivityUpdatedEvent(activity=self._finalize_message(record, item))]
        if isinstance(item, ResponseFunctionToolCall):
            return [ActivityUpdatedEvent(activity=self._finalize_function_call(record, item))]
        if isinstance(item, ResponseReasoningItem):
            return self._finalize_reasoning(record, item)
        return []

    def _finalize_message(self, record: _StreamItem, item: ResponseOutputMessage) -> SessionActivity:
        text_parts: list[str] = []
        refusal_parts: list[str] = []
        for part in item.content:
            if isinstance(part, ResponseOutputText):
                text_parts.append(part.text)
            elif isinstance(part, ResponseOutputRefusal):
                refusal_parts.append(part.refusal)

        if refusal_parts:
            return ErrorActivity(
                id=record.activity_id, state="error", error_type="assistant_refusal", detail="".join(refusal_parts)
            )
        return AssistantActivity(id=record.activity_id, state="complete", content="".join(text_parts))

    def _finalize_function_call(self, record: _StreamItem, item: ResponseFunctionToolCall) -> TaskActivity:
        # Prefer the authoritative arguments from the final item; fall back to the streamed snapshot.
        arguments = _parse_arguments(item.arguments)
        if arguments is None and record.emitted_arguments:
            arguments = record.emitted_arguments
        return TaskActivity(
            id=record.activity_id, state="complete", name=item.name, permission="accepted", arguments=arguments
        )

    def _finalize_reasoning(self, record: _StreamItem, item: ResponseReasoningItem) -> list[StreamingEvent]:
        # Prefer the authoritative summary from the final item; fall back to the streamed parts.
        content = "\n".join(summary.text for summary in item.summary) or _reasoning_content(record)
        if not content:
            return []  # Mirror the non-streaming converter, which skips reasoning with no content.

        activity = ReasoningActivity(id=record.activity_id, state="complete", content=content)
        # No summary streamed, but the final item carries content: emit it as a complete activity.
        if not record.created:
            return [ActivityCreatedEvent(activity=activity)]
        return [ActivityUpdatedEvent(activity=activity)]


def is_terminal_error(event: ResponseStreamEvent) -> bool:
    """Whether this event signals the stream terminated with an error.

    The interop provider only finalizes on a completed event; for these terminal-error events it
    ends the stream without yielding a RouterResponse, so the caller should stop consuming after
    handling them rather than waiting for a response that never arrives.
    """
    return isinstance(event, ResponseErrorEvent | ResponseFailedEvent | ResponseIncompleteEvent)


def error_event(error_type: str, detail: str) -> ActivityCreatedEvent:
    """Build a client event for an error raised outside the normal event stream (e.g. a router exception)."""
    return ActivityCreatedEvent(activity=_make_error_activity(error_type, detail))


def _error_activity(event: ResponseErrorEvent | ResponseFailedEvent | ResponseIncompleteEvent) -> ErrorActivity:
    if isinstance(event, ResponseErrorEvent):
        return _make_error_activity(event.code or "stream_error", event.message)
    if isinstance(event, ResponseFailedEvent):
        error = event.response.error
        if error is not None:
            return _make_error_activity(error.code, error.message)
        return _make_error_activity("response_failed", "The response failed.")

    details = event.response.incomplete_details
    reason = details.reason if details and details.reason else "unknown"
    return _make_error_activity("incomplete", f"The response was incomplete: {reason}.")


def _make_error_activity(error_type: str, detail: str) -> ErrorActivity:
    return ErrorActivity(id=str(uuid.uuid4()), state="error", error_type=error_type, detail=detail)


def _reasoning_content(record: _StreamItem) -> str:
    """Join the accumulated reasoning summary parts in index order, matching the final item."""
    return "\n".join(record.summary_parts[index] for index in sorted(record.summary_parts))


def _parse_partial_arguments(raw: str) -> dict[str, Any]:
    """Best-effort parse of an incomplete function-call arguments JSON string.

    `trailing-strings` keeps the last in-progress string value so it can stream into the client
    character by character. Returns an empty mapping when nothing parseable has arrived yet.
    """
    try:
        result = jiter.from_json(raw.encode(), partial_mode="trailing-strings")
    except ValueError:
        return {}
    return result if isinstance(result, dict) else {}


def _parse_arguments(raw: str) -> dict[str, Any] | None:
    """Parse a complete function-call arguments JSON string, or None if absent or invalid."""
    if not raw:
        return None
    try:
        result = jiter.from_json(raw.encode())
    except ValueError:
        return None
    return result if isinstance(result, dict) else None
