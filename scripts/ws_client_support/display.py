"""Accumulate and render agent streaming events.
Fully AI-generated"""

import asyncio
import json
import sys

from pydantic import TypeAdapter, ValidationError

from agent_server.schemas.activity import (
    ActivityCreatedEvent,
    ActivityDeltaEvent,
    ActivityUpdatedEvent,
    AssistantActivity,
    ErrorActivity,
    InfoActivity,
    InfoEvent,
    InputRequestActivity,
    ReasoningActivity,
    SessionActivity,
    SessionConfigChangedEvent,
    StatusEvent,
    StreamingEvent,
    TaskActivity,
    UserActivity,
)

_STREAMING_EVENT_ADAPTER = TypeAdapter(StreamingEvent)
_DISPLAYED_STATUSES = {
    "agent_starting",
    "agent_ready",
    "agent_cancelling",
    "agent_cancelled",
    "agent_stopping",
    "agent_stopped",
    "agent_turn_ended",
}


class Terminal:
    def __init__(self, *, interactive: bool) -> None:
        self._interactive = interactive
        self._lock = asyncio.Lock()
        self._prompt_visible = False

    async def show_prompt(self) -> None:
        if not self._interactive:
            return
        async with self._lock:
            sys.stdout.write("client> ")
            sys.stdout.flush()
            self._prompt_visible = True

    async def consume_prompt(self) -> None:
        async with self._lock:
            self._prompt_visible = False

    async def write_line(self, line: str) -> None:
        await self.write_lines([line])

    async def write_lines(self, lines: list[str]) -> None:
        if not lines:
            return
        async with self._lock:
            self._prepare_line()
            sys.stdout.write("\n".join(lines))
            sys.stdout.write("\n")
            sys.stdout.flush()

    def _prepare_line(self) -> None:
        if self._prompt_visible:
            sys.stdout.write("\n")
            self._prompt_visible = False


class EventDisplay:
    def __init__(self, terminal: Terminal) -> None:
        self._terminal = terminal
        self._activities: dict[tuple[str, str], SessionActivity] = {}
        self._statuses: dict[str, str] = {}
        self._rendered_task_states: set[tuple[str, str, str, str]] = set()

    async def load_activities(self, activities: list[SessionActivity]) -> None:
        for activity in activities:
            self._activities[(activity.agent_id, activity.id)] = activity
            await self._render_created(activity)

    def get_pending_input_request(self, agent_id: str, request_id: str) -> InputRequestActivity | None:
        activity = self._activities.get((agent_id, request_id))
        if isinstance(activity, InputRequestActivity) and activity.state == "in_progress":
            return activity
        return None

    async def handle_message(self, message: str | bytes) -> None:
        text = message if isinstance(message, str) else message.decode("utf-8", errors="replace")
        try:
            event = _STREAMING_EVENT_ADAPTER.validate_json(text)
        except ValidationError as exc:
            await self._terminal.write_lines(
                [
                    "[protocol error] server message did not match StreamingEvent",
                    str(exc),
                ]
            )
            return
        await self._handle_event(event)

    async def _handle_event(self, event: StreamingEvent) -> None:
        match event:
            case StatusEvent():
                await self._render_status(event)
            case ActivityCreatedEvent():
                activity = event.activity
                self._activities[(activity.agent_id, activity.id)] = activity
                await self._render_created(activity)
            case ActivityDeltaEvent():
                await self._handle_delta(event)
            case ActivityUpdatedEvent():
                activity = event.activity
                self._activities[(activity.agent_id, activity.id)] = activity
                await self._render_updated(activity)
            case SessionConfigChangedEvent():
                await self._terminal.write_line(f"[config][{event.agent_id}] {event.config_key}={event.new_value}")
            case InfoEvent():
                await self._terminal.write_lines(
                    _content_lines(f"[info][{event.agent_id}] {event.title}: ", event.content)
                )

    async def _render_status(self, event: StatusEvent) -> None:
        if event.status_id not in _DISPLAYED_STATUSES:
            return
        if self._statuses.get(event.agent_id) == event.status_id:
            return
        self._statuses[event.agent_id] = event.status_id
        await self._terminal.write_line(f"[status][{event.agent_id}] {event.status_id.replace('_', ' ')}")

    async def _handle_delta(self, event: ActivityDeltaEvent) -> None:
        key = (event.agent_id, event.activity_id)
        activity = self._activities.get(key)
        if activity is None:
            return

        delta = event.delta
        if delta.content_delta is not None and isinstance(
            activity, UserActivity | AssistantActivity | ReasoningActivity
        ):
            activity.content += delta.content_delta
        if delta.argument_delta is not None and isinstance(activity, TaskActivity):
            arguments = dict(activity.arguments or {})
            arguments[delta.argument_delta.key] = delta.argument_delta.value
            activity.arguments = arguments
        if delta.result_delta is not None and isinstance(activity, TaskActivity):
            activity.result = (activity.result or "") + delta.result_delta
        if delta.permission is not None and isinstance(activity, TaskActivity):
            activity.permission = delta.permission

    async def _render_created(self, activity: SessionActivity) -> None:
        match activity:
            case UserActivity():
                await self._terminal.write_lines(_content_lines(_activity_prefix(activity), activity.content))
            case AssistantActivity() | ReasoningActivity():
                if activity.state != "in_progress":
                    await self._terminal.write_lines(_content_lines(_activity_prefix(activity), activity.content))
            case TaskActivity():
                await self._render_task(activity)
            case InputRequestActivity():
                await self._render_input_request(activity)
            case InfoActivity():
                await self._terminal.write_lines(
                    _content_lines(
                        f"[info activity][{activity.agent_id}][{activity.id}] {activity.title}: ",
                        activity.content,
                    )
                )
            case ErrorActivity():
                await self._terminal.write_lines(
                    _content_lines(
                        f"[error][{activity.agent_id}][{activity.id}] {activity.error_type}: ",
                        activity.detail,
                    )
                )

    async def _render_updated(self, activity: SessionActivity) -> None:
        match activity:
            case AssistantActivity() | ReasoningActivity():
                if activity.state != "in_progress":
                    await self._terminal.write_lines(_content_lines(_activity_prefix(activity), activity.content))
            case TaskActivity():
                await self._render_task(activity)
            case InputRequestActivity():
                await self._render_input_request(activity)
            case _:
                await self._render_created(activity)

    async def _render_task(self, activity: TaskActivity) -> None:
        is_final = activity.state != "in_progress"
        if not is_final and activity.permission != "pending":
            return

        rendered_state = (activity.agent_id, activity.id, activity.state, activity.permission)
        if rendered_state in self._rendered_task_states:
            return
        self._rendered_task_states.add(rendered_state)

        lines = [
            f"[task][{activity.agent_id}][{activity.id}] {activity.name} "
            f"{activity.state}; permission={activity.permission}"
        ]
        if activity.arguments:
            lines.append(f"  arguments: {json.dumps(activity.arguments, ensure_ascii=True, sort_keys=True)}")
        if activity.result:
            lines.extend(
                _content_lines(
                    "  result: ",
                    activity.result,
                )
            )
        if activity.permission == "pending":
            lines.append(f"  respond: /permission {activity.agent_id} {activity.id} <accepted|denied>")
        await self._terminal.write_lines(lines)

    async def _render_input_request(self, activity: InputRequestActivity) -> None:
        lines = [f"[input request][{activity.agent_id}][{activity.id}] {activity.title} ({activity.state})"]
        for item in activity.items:
            lines.append(f"  [{item.id}] {item.header}")
            lines.extend(f"    {line}" for line in item.content.splitlines())
            if item.options:
                lines.append("    options:")
                lines.extend(f"      {option_id}: {label}" for option_id, label in item.options.items())
            if item.selections is not None:
                lines.append(f"    selections: {', '.join(item.selections)}")
        if activity.state == "in_progress":
            assignments = " ".join(f"{item.id}=<selection>" for item in activity.items)
            lines.append(f"  respond: /input {activity.agent_id} {activity.id} {assignments}")
            if any(item.allow_multiple for item in activity.items):
                lines.append("  Repeat an item=<selection> assignment to select multiple values.")
        await self._terminal.write_lines(lines)


def _activity_prefix(activity: UserActivity | AssistantActivity | ReasoningActivity) -> str:
    return f"[{activity.type}][{activity.agent_id}][{activity.id}] "


def _content_lines(prefix: str, content: str) -> list[str]:
    content_lines = content.splitlines() or [""]
    return [f"{prefix}{content_lines[0]}", *(f"  {line}" for line in content_lines[1:])]
