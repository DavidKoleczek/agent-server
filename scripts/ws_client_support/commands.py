"""Parse interactive commands into typed client events.
Fully AI-generated"""

from dataclasses import dataclass
import shlex
from typing import Protocol

from agent_server.schemas.activity import (
    CancelEvent,
    ClientEvent,
    InputRequestActivity,
    InputRequestResponseEvent,
    InputRequestResponseItem,
    ModeChangeEvent,
    PermissionChangeEvent,
    QuitEvent,
    SessionConfigChangeEvent,
    UserMessageEvent,
)

_HELP = """Commands:
/mode <default|plan>
/input <agent-id> <request-id> <item-id>=<selection> [<item-id>=<selection> ...]
/permission <agent-id> <task-id> <accepted|denied|pending|not_determined>
/config <tool_preset|model> <value>
/cancel
/quit
/exit
/help

The /input command must answer every item in the request. Repeat an item assignment for multiple
selections. Quote the complete item=selection argument when a selection contains spaces.
Any other text is sent as a user message."""


class InputRequestLookup(Protocol):
    def get_pending_input_request(self, agent_id: str, request_id: str) -> InputRequestActivity | None: ...


class CommandError(ValueError):
    pass


@dataclass(frozen=True)
class CommandResult:
    event: ClientEvent | None = None
    message: str | None = None
    close_local: bool = False
    stop_input: bool = False


class CommandProcessor:
    def __init__(self, input_requests: InputRequestLookup) -> None:
        self._input_requests = input_requests

    def parse(self, text: str) -> CommandResult:
        if not text.startswith("/"):
            return CommandResult(event=UserMessageEvent(content=text))

        try:
            parts = shlex.split(text)
        except ValueError as exc:
            raise CommandError(f"Could not parse command: {exc}") from exc
        if not parts:
            raise CommandError("Command is empty.")

        command = parts[0].lower()
        arguments = parts[1:]
        match command:
            case "/help":
                _require_count(arguments, 0, "/help")
                return CommandResult(message=_HELP)
            case "/exit":
                _require_count(arguments, 0, "/exit")
                return CommandResult(close_local=True, stop_input=True)
            case "/cancel":
                _require_count(arguments, 0, "/cancel")
                return CommandResult(event=CancelEvent())
            case "/quit":
                _require_count(arguments, 0, "/quit")
                return CommandResult(event=QuitEvent(), stop_input=True)
            case "/mode":
                return CommandResult(event=_mode_event(arguments))
            case "/permission":
                return CommandResult(event=_permission_event(arguments))
            case "/config":
                return CommandResult(event=_config_event(arguments))
            case "/input":
                return self._input_event(arguments)
            case _:
                raise CommandError(f"Unknown command: {parts[0]}. Use /help to list commands.")

    def _input_event(self, arguments: list[str]) -> CommandResult:
        if len(arguments) < 3:
            raise CommandError(
                "Usage: /input <agent-id> <request-id> <item-id>=<selection> [<item-id>=<selection> ...]"
            )

        agent_id, request_id, *assignments = arguments
        request = self._input_requests.get_pending_input_request(agent_id, request_id)
        if request is None:
            raise CommandError(f"Pending input request does not exist: {agent_id}/{request_id}")

        request_items = {item.id: item for item in request.items}
        responses: dict[str, list[str]] = {}
        for assignment in assignments:
            item_id, separator, selection = assignment.partition("=")
            if not separator or not item_id or not selection:
                raise CommandError(f"Input answer must use item=selection: {assignment}")
            item = request_items.get(item_id)
            if item is None:
                raise CommandError(f"Input item does not exist on request {request_id}: {item_id}")
            responses.setdefault(item_id, []).append(selection)

        missing = [item.id for item in request.items if item.id not in responses]
        if missing:
            raise CommandError(f"The response must answer every item. Missing: {', '.join(missing)}")

        for item in request.items:
            if not item.allow_multiple and len(responses[item.id]) != 1:
                raise CommandError(f"Input item {item.id} accepts exactly one selection.")

        response_items = [
            InputRequestResponseItem(id=candidate.id, selections=responses[candidate.id]) for candidate in request.items
        ]
        return CommandResult(
            event=InputRequestResponseEvent(
                agent_id=agent_id,
                id=request_id,
                items=response_items,
            ),
            message=f"[client] submitting input request: {agent_id}/{request_id}",
        )


def _mode_event(arguments: list[str]) -> ModeChangeEvent:
    _require_count(arguments, 1, "/mode <default|plan>")
    mode = arguments[0].lower()
    if mode not in ("default", "plan"):
        raise CommandError("Mode must be default or plan.")
    return ModeChangeEvent(new_mode=mode)


def _permission_event(arguments: list[str]) -> PermissionChangeEvent:
    _require_count(
        arguments,
        3,
        "/permission <agent-id> <task-id> <accepted|denied|pending|not_determined>",
    )
    agent_id, task_id, permission = arguments
    permission = permission.lower()
    if permission not in ("accepted", "denied", "pending", "not_determined"):
        raise CommandError("Permission must be accepted, denied, pending, or not_determined.")
    return PermissionChangeEvent(agent_id=agent_id, id=task_id, permission=permission)


def _config_event(arguments: list[str]) -> SessionConfigChangeEvent:
    _require_count(arguments, 2, "/config <tool_preset|model> <value>")
    config_key, new_value = arguments
    config_key = config_key.lower()
    if config_key not in ("tool_preset", "model"):
        raise CommandError("Config key must be tool_preset or model.")
    return SessionConfigChangeEvent(config_key=config_key, new_value=new_value)


def _require_count(arguments: list[str], expected: int, usage: str) -> None:
    if len(arguments) != expected:
        raise CommandError(f"Usage: {usage}")
