import asyncio
import copy
from datetime import datetime
import inspect
import json
import os
from pathlib import Path
import re
from typing import cast
import uuid
from uuid import uuid4

from anthropic import AsyncAnthropic
from google import genai
from interop_router.router import Router
from interop_router.types import ChatMessage, InteropRouterError, RouterResponse, RouterStream, SupportedModel
from liquid import render
from openai import AsyncOpenAI
from openai.types.responses import EasyInputMessageParam
from openai.types.responses.response_function_tool_call_param import ResponseFunctionToolCallParam
from openai.types.responses.response_input_item_param import FunctionCallOutput
from openai.types.responses.tool_param import ToolParam
from openai.types.shared_params import Reasoning
from pydantic import ValidationError

from agent_server.agent.activity_converter import function_call_item_to_activity, response_to_activities
from agent_server.agent.activity_stream_converter import ActivityStreamConverter, error_event, is_terminal_error
from agent_server.agent.prompts.system_prompt import SYSTEM_PROMPT
from agent_server.agent.prompts.tool import TOOL_AUTO_DENIED, TOOL_FAILED, TOOL_MISSING, TOOL_USER_DENIED
from agent_server.core.hooks import git, system_info
from agent_server.core.subagent.tool import TOOL_NAME as AGENT_TOOL_TOOL_NAME
from agent_server.core.subagent.tool import AgentTool
from agent_server.core.tools._protocol import Tool
from agent_server.core.tools._utils import ConstraintPolicy
from agent_server.core.tools.presets import permissive_tools, standard_tools
from agent_server.schemas.activity import (
    ActivityCreatedEvent,
    ActivityUpdatedEvent,
    ClientEvent,
    PermissionChangeEvent,
    SessionActivity,
    SessionConfigChangedEvent,
    SessionConfigChangeEvent,
    StatusEvent,
    StreamingEvent,
    TaskActivity,
    TaskPermission,
    UserActivity,
    UserMessageEvent,
)
from agent_server.schemas.agent_config import AgentConfig
from agent_server.schemas.session import SessionChatMessage, SessionConfig
from agent_server.storage.session_store import SessionStore


class Agent:
    def __init__(
        self,
        config: AgentConfig,
        client_events: asyncio.Queue[ClientEvent],
        streaming_events: asyncio.Queue[StreamingEvent],
        agent_id: str = "main",
    ) -> None:
        self.config = config
        self.client_events = client_events
        self.streaming_events = streaming_events
        self.agent_id = agent_id

        self._session_database = self._resolve_session_database()
        self._session_store = SessionStore(self._session_database)
        self.history: list[SessionChatMessage] = self._session_store.load_session_chat_messages(agent_id=self.agent_id)
        self.activities: list[SessionActivity] = self._session_store.load_activities(agent_id=self.agent_id)
        self.session_config = self._session_store.load_or_create_session_config(self.config.default_model)

        self.router = Router()
        self.router.register("openai", AsyncOpenAI())
        self.router.register("gemini", genai.Client(api_key=os.getenv("GEMINI_API_KEY")))
        self.router.register("anthropic", AsyncAnthropic())

        self.model: SupportedModel = self.session_config.model

        self._agent_tool = (
            AgentTool(
                streaming_events=self.streaming_events,
                config=self.config.model_copy(update={"session_database": self._session_database}),
            )
            if self.agent_id == "main"
            else None
        )
        self._apply_tool_preset(self.session_config.tool_preset)

        self._should_run_agent = asyncio.Event()
        self._should_run_agent.set()
        # Tracks which assistant response already emitted agent_turn_ended so unrelated wake-ups do not emit it again.
        self._last_ended_assistant_id: str | None = None

        # Does not allow user messages to be appended to the history while the llm request is processing to prevent things from happening out of order.
        self._history_lock = asyncio.Lock()

    async def start(self) -> None:
        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(self._client_event_loop())
            tasks.create_task(self._agent_event_loop())

    async def _agent_event_loop(self) -> None:
        while True:
            # Waits until some signal that we should take a step in the agent's processing loop.
            await self._should_run_agent.wait()
            self._should_run_agent.clear()
            await self.streaming_events.put(StatusEvent(status_id="agent_running"))

            while True:
                tool_tasks: list[asyncio.Task[bool]] = []
                pending_tool_calls = self._get_pending_tool_calls()
                if pending_tool_calls:
                    async with asyncio.TaskGroup() as tasks:
                        for pending_tool_call in pending_tool_calls:
                            tool_tasks.append(tasks.create_task(self._execute_tool_call(pending_tool_call)))

                tools_made_progress = any(task.result() for task in tool_tasks)
                llm_made_progress = await self._llm_step()
                # If neither made any progress, we break and wait until something happens to prevent the agent from constantly looping.
                if not tools_made_progress and not llm_made_progress:
                    if self._should_run_agent.is_set():
                        self._should_run_agent.clear()
                        continue

                    completed_turn_assistant_id = self._completed_turn_assistant_id()
                    # Emit the turn-ended event once for each assistant response that completes a turn.
                    if (
                        completed_turn_assistant_id is not None
                        and completed_turn_assistant_id != self._last_ended_assistant_id
                    ):
                        self._last_ended_assistant_id = completed_turn_assistant_id
                        self.streaming_events.put_nowait(
                            StatusEvent(agent_id=self.agent_id, status_id="agent_turn_ended")
                        )
                    break

    async def _client_event_loop(self) -> None:
        while True:
            # Block until there is a client event to process
            client_events = await self._drain_client_events()
            # Based on the client events, make the appropriate updates to the state
            for event in client_events:
                match event:
                    case UserMessageEvent(content=content):
                        async with self._history_lock:
                            self._append_chat_message(
                                ChatMessage(message=EasyInputMessageParam(role="user", content=content))
                            )
                            self._append_activity(UserActivity(id=str(uuid.uuid4()), state="complete", content=content))
                    case PermissionChangeEvent():
                        if event.agent_id == self.agent_id:
                            self._handle_permission_change(event)
                        elif self._agent_tool is None or not await self._agent_tool.submit_event(
                            event.agent_id,
                            event,
                        ):
                            self.streaming_events.put_nowait(
                                error_event(
                                    "unknown_agent",
                                    f"Agent is not running: {event.agent_id}",
                                )
                            )
                    case SessionConfigChangeEvent():
                        self._handle_config_change(event)
                        if self._agent_tool is not None:
                            await self._agent_tool.broadcast_event(event)
            # Putting it outside the for loop is a minor optimization to not signal the agent too often that likely won't matter.
            self._should_run_agent.set()

    async def _execute_tool_call(self, tool_call: SessionChatMessage) -> bool:
        """Executes a tool call.

        Returns True, indicating some state was changed, if:
        - A tool output message was added indicating that a tool is longer currently available
        - The actual result of the executing the tool was added to the history and the activity was updated
        """

        # Get the variables we need
        call_id = str(tool_call.chat_message.message.get("call_id", ""))
        arguments = json.loads(str(tool_call.chat_message.message.get("arguments", "{}")))
        tool_name = str(tool_call.chat_message.message.get("name", ""))
        tool = self._tools_by_name.get(tool_name)

        # Get the associated TaskActivity, and create it if it does not exist for some reason
        task_activity = next((a for a in self.activities if isinstance(a, TaskActivity) and a.id == call_id), None)
        if task_activity is None:
            task_activity = function_call_item_to_activity(tool_call.chat_message)
            task_activity.permission = tool_call.permission or "not_determined"
            self._append_activity(task_activity)
            self.streaming_events.put_nowait(ActivityCreatedEvent(activity=task_activity))

        # If the tool no longer exists, make a tool output message using the TOOL_MISSING message to indicate that the tool is no longer available to the agent.
        if tool is None:
            tool_call.permission = "accepted"
            self._session_store.update_chat_message_permission(tool_call.chat_message.id, tool_call.permission)
            self._append_chat_message(
                ChatMessage(
                    message=FunctionCallOutput(
                        call_id=call_id,
                        type="function_call_output",
                        output=TOOL_MISSING,
                    )
                ),
                permission=tool_call.permission,
            )

            task_activity.state = "complete"
            task_activity.permission = tool_call.permission
            task_activity.result = TOOL_MISSING
            self._session_store.update_activity(task_activity)
            self.streaming_events.put_nowait(ActivityUpdatedEvent(activity=task_activity))
            return True

        permission = tool_call.permission
        tool_output = None
        if permission == "accepted":
            tool_policy = ConstraintPolicy.ALLOW
        elif permission == "denied":
            tool_policy = ConstraintPolicy.DENY
        elif permission == "pending":
            tool_policy = ConstraintPolicy.ASK
        else:
            try:
                tool_policy = tool.check_constraint(**arguments)
            except Exception as exc:
                tool_output = render(
                    TOOL_FAILED,
                    error_message=str(exc) or type(exc).__name__,
                )
                tool_call.permission = "accepted"
                self._session_store.update_chat_message_permission(tool_call.chat_message.id, tool_call.permission)
                task_activity.state = "error"
                task_activity.permission = tool_call.permission
                task_activity.result = tool_output
                tool_policy = None

        if tool_policy is None:
            pass
        elif tool_policy == ConstraintPolicy.DENY:
            # Case where the tool call is automatically denied.
            tool_output = TOOL_USER_DENIED if permission else TOOL_AUTO_DENIED
            tool_call.permission = "denied"
            self._session_store.update_chat_message_permission(tool_call.chat_message.id, tool_call.permission)
            task_activity.state = "complete"
            task_activity.permission = tool_call.permission
            task_activity.result = tool_output
        elif tool_policy == ConstraintPolicy.ASK:
            # Case where the tool call requires user approval.
            tool_call.permission = "pending"
            self._session_store.update_chat_message_permission(tool_call.chat_message.id, tool_call.permission)
            task_activity.state = "in_progress"
            task_activity.permission = tool_call.permission
        elif tool_policy == ConstraintPolicy.ALLOW:
            # Case where the tool call is automatically allowed.
            tool_call.permission = "accepted"
            self._session_store.update_chat_message_permission(tool_call.chat_message.id, tool_call.permission)
            task_activity.permission = tool_call.permission
            # Immediately let the client know that the tool is auto accepted and will be executed.
            task_updated_event = ActivityUpdatedEvent(activity=task_activity)
            self._session_store.update_activity(task_activity)
            self.streaming_events.put_nowait(task_updated_event)

            # Execute the tool
            self.streaming_events.put_nowait(StatusEvent(status_id="executing_tool"))
            try:
                raw_tool_output = tool.execute(**arguments)
                if inspect.iscoroutine(raw_tool_output):
                    raw_tool_output = await raw_tool_output
                if isinstance(raw_tool_output, str):
                    tool_output = raw_tool_output
                elif isinstance(raw_tool_output, list):
                    tool_output = json.dumps(raw_tool_output)
                else:
                    tool_output = str(raw_tool_output)
            except Exception as exc:
                tool_output = render(
                    TOOL_FAILED,
                    error_message=str(exc) or type(exc).__name__,
                )
                task_activity.state = "error"
            else:
                task_activity.state = "complete"
            task_activity.result = tool_output

        # Add the tool output to the history
        made_progress = tool_output is not None and task_activity.permission != "pending"
        if made_progress:
            output_message = ChatMessage(
                message=FunctionCallOutput(call_id=call_id, type="function_call_output", output=tool_output)
            )
            self._append_chat_message(output_message, permission=task_activity.permission)

        # Update the task_activity to reflect the new state
        self._session_store.update_activity(task_activity)
        task_updated_event = ActivityUpdatedEvent(activity=task_activity)
        self.streaming_events.put_nowait(task_updated_event)
        return made_progress

    def _get_pending_tool_calls(self) -> list[SessionChatMessage]:
        """Return function calls that do not have a matching output."""
        function_call_output_ids = {
            message.chat_message.message.get("call_id")
            for message in self.history
            if message.chat_message.message.get("type") == "function_call_output"
        }
        return [
            message
            for message in self.history
            if message.chat_message.message.get("type") == "function_call"
            and message.chat_message.message.get("call_id") not in function_call_output_ids
            and message.permission != "pending"
        ]

    def _completed_turn_assistant_id(self) -> str | None:
        # A turn is not complete until every function call has a corresponding output.
        function_call_output_ids = {
            message.chat_message.message.get("call_id")
            for message in self.history
            if message.chat_message.message.get("type") == "function_call_output"
        }
        if any(
            message.chat_message.message.get("call_id") not in function_call_output_ids
            for message in self.history
            if message.chat_message.message.get("type") == "function_call"
        ):
            return None

        # Reasoning and tool messages do not determine who spoke last, so find the latest user or assistant message.
        latest_role_message = next(
            (
                message
                for message in reversed(self.history)
                if message.chat_message.message.get("role") in {"user", "assistant"}
            ),
            None,
        )
        # The turn is complete only when the assistant was the most recent speaker.
        if latest_role_message is None or latest_role_message.chat_message.message.get("role") != "assistant":
            return None

        return latest_role_message.chat_message.id

    async def _llm_step(self) -> bool:
        async with self._history_lock:
            return await self._llm_step_locked()

    async def _llm_step_locked(self) -> bool:
        """Gets a response from the LLM.

        Returns True indicating that a response was obtained and successfully processed.
        False otherwise
        """
        # Check if there is a user message without an assistant message, return otherwise
        if not self.history:
            return False
        if self.history[-1].chat_message.message.get("role") == "assistant":
            return False

        # Check if all tool calls have paired tool responses
        function_call_output_ids = {
            message.chat_message.message.get("call_id")
            for message in self.history
            if message.chat_message.message.get("type") == "function_call_output"
        }
        if any(
            message.permission not in {"accepted", "denied"}
            or message.chat_message.message.get("call_id") not in function_call_output_ids
            for message in self.history
            if message.chat_message.message.get("type") == "function_call"
        ):
            return False

        working_dir = str(self.config.working_dir)
        system_prompt = render(
            SYSTEM_PROMPT,
            working_directory=working_dir,
            is_git_repo=git.is_git_repo(working_dir),
            platform=system_info.platform(),
            os_version=system_info.os_version(),
            current_date=system_info.todays_date(tz="America/New_York"),  # TODO
            model_friendly_name=self.model,  # TODO
            model_id=self.model,  # TODO
            knowledge_cutoff="Aug 2025",  # TODO
            current_branch=git.current_branch(working_dir) or "N/A",
            main_branch=git.main_branch(working_dir) or "N/A",
            git_status=git.git_status(working_dir) or "N/A",
            recent_commits=git.recent_commits(working_dir) or "N/A",
        )

        # Create a copy of history to modify for the model call.
        model_input = copy.deepcopy([x.chat_message for x in self.history])
        model_input.insert(0, ChatMessage(message=EasyInputMessageParam(role="system", content=system_prompt)))

        # Call the model and stream events to the caller.
        self._refresh_tool_definitions()
        stream = await self.router.create(
            input=model_input,
            model=self.model,
            stream=True,
            reasoning=Reasoning(effort="medium", summary="auto"),
            include=["reasoning.encrypted_content", "web_search_call.results", "web_search_call.action.sources"],
            tools=self._request_tools,
            max_output_tokens=120_000,
        )
        self.streaming_events.put_nowait(StatusEvent(status_id="waiting_for_llm_response"))

        # Converts the stream of OpenAI streaming events coming from interop-router into streaming events for the client and returns the final RouterResponse.
        response = await self._handle_router_stream(stream, self.streaming_events)
        self.streaming_events.put_nowait(StatusEvent(status_id="processing_llm_response"))
        if response is None:
            return False

        # Update the activites based on the new response.
        [self._append_activity(activity) for activity in response_to_activities(response)]

        # Add the chat messages to the history.
        for msg in response.output:
            self._append_chat_message(msg)
        return True

    async def _handle_router_stream(
        self, stream: RouterStream, agent_activities: asyncio.Queue[StreamingEvent]
    ) -> RouterResponse | None:
        """Processes the stream of events from the router call into activities that are sent to the client.
        Returns the final RouterResponse when the stream is done, or None if the stream ended without a RouterResponse.
        """
        converter = ActivityStreamConverter()
        try:
            async for event in stream:
                if isinstance(event, RouterResponse):
                    # RouterResponse is the final event after streaming is done.
                    # For any call to the agent (sub-agent) tool, we generate a sub_agent_id and attach it to the function call arguments.
                    event = await self._add_sub_agent_id_to_tasks(event)
                    return event
                for streaming_event in converter.handle(event):
                    await agent_activities.put(streaming_event)
                # On a terminal error the provider ends the stream without a RouterResponse, so stop here.
                if is_terminal_error(event):
                    return None
        except InteropRouterError as exc:
            await agent_activities.put(error_event("router_error", str(exc)))
            return None
        return None

    async def _add_sub_agent_id_to_tasks(self, response: RouterResponse) -> RouterResponse:
        for message in response.output:
            item = message.message
            if item.get("type") != "function_call" or item.get("name") != AGENT_TOOL_TOOL_NAME:
                continue

            function_call = cast(ResponseFunctionToolCallParam, item)
            arguments = json.loads(function_call["arguments"])

            arguments["sub_agent_id"] = f"sub-{str(uuid4())[:8]}"
            function_call["arguments"] = json.dumps(arguments)

        return response

    def close(self) -> None:
        self._session_store.close()

    def _resolve_session_database(self) -> Path:
        """Creates a session db path if one was not provided"""
        if self.config.session_database is not None:
            return self.config.session_database

        sessions_dir = self.config.working_dir / ".agents" / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)

        working_dir_name = self.config.working_dir.name
        sanitized_name = re.sub(r'[<>:"/\\|?*\s]', "_", working_dir_name)

        date_str = datetime.now().strftime("%Y-%m-%d-%H%M%S")
        short_uuid = str(uuid.uuid4())[:8]
        filename = f"{sanitized_name}_{date_str}_{short_uuid}.sqlite"

        return sessions_dir / filename

    async def _drain_client_events(self) -> list[ClientEvent]:
        client_events = [await self.client_events.get()]
        while True:
            try:
                client_events.append(self.client_events.get_nowait())
            except asyncio.QueueEmpty:
                break
        return client_events

    def _append_chat_message(
        self, message: ChatMessage, permission: TaskPermission | None = None
    ) -> SessionChatMessage:
        """Processes the given ChatMessage into the session store and in-memory history."""
        position = len(self.history)
        self._session_store.add_chat_message(position, message, permission=permission, agent_id=self.agent_id)
        session_message = SessionChatMessage(
            position=position, permission=permission, agent_id=self.agent_id, chat_message=message
        )
        self.history.append(session_message)
        return session_message

    def _append_activity(self, activity: SessionActivity) -> SessionActivity:
        """Processes the given activity into the session store and in-memory activities"""
        position = len(self.activities)
        activity.agent_id = self.agent_id
        self._session_store.save_activity(position, activity, agent_id=self.agent_id)
        self.activities.append(activity)
        return activity

    def _apply_tool_preset(self, tool_preset: str) -> None:
        """Rebuild the active tool set and its request/lookup indices for the given preset."""
        tools = []
        if tool_preset == "permissive":
            tools = permissive_tools(self.config.working_dir)
        elif tool_preset == "standard":
            tools = standard_tools(self.config.working_dir)

        if self._agent_tool is not None:
            tools.append(self._agent_tool)

        self.tools = tools
        self._refresh_tool_definitions()

    def _refresh_tool_definitions(self) -> None:
        """Refresh the model request and tool lookup definitions from one snapshot."""
        tool_definitions = [(tool, tool.get_tool_defs()) for tool in self.tools]
        self._request_tools: list[ToolParam] = [
            definition for _, definitions in tool_definitions for definition in definitions.values()
        ]
        self._tools_by_name: dict[str, Tool] = {
            name: tool for tool, definitions in tool_definitions for name in definitions
        }

    def _get_function_call_by_id(self, call_id: str) -> ChatMessage | None:
        tool_call_msg = next(
            (
                m.chat_message
                for m in self.history
                if m.chat_message.message.get("type") == "function_call"
                and m.chat_message.message.get("call_id") == call_id
            ),
            None,
        )
        return tool_call_msg

    def _handle_permission_change(self, event: PermissionChangeEvent) -> None:
        session_message = next(
            (
                message
                for message in self.history
                if message.chat_message.message.get("type") == "function_call"
                and message.chat_message.message.get("call_id") == event.id
            ),
            None,
        )
        task_activity = next(
            (
                activity
                for activity in self.activities
                if isinstance(activity, TaskActivity) and activity.id == event.id
            ),
            None,
        )

        if (session_message is None) or (task_activity is None):
            return

        session_message.permission = event.permission
        task_activity.permission = event.permission
        self._session_store.update_chat_message_permission(session_message.chat_message.id, event.permission)
        self._session_store.update_activity(task_activity)
        self.streaming_events.put_nowait(ActivityUpdatedEvent(activity=task_activity))

    def _handle_config_change(self, event: SessionConfigChangeEvent) -> None:
        """Validate a client-requested config change, persist it, and apply it to live state."""
        updated = {**self.session_config.model_dump(), event.config_key: event.new_value}
        try:
            self.session_config = SessionConfig.model_validate(updated)
        except ValidationError:
            self.streaming_events.put_nowait(
                error_event("invalid_config", f"Invalid value for {event.config_key}: {event.new_value}")
            )
            return

        self._session_store.update_session_config(self.session_config)

        match event.config_key:
            case "model":
                self.model = self.session_config.model
            case "tool_preset":
                self._apply_tool_preset(self.session_config.tool_preset)

        applied_value = str(getattr(self.session_config, event.config_key))
        self.streaming_events.put_nowait(
            SessionConfigChangedEvent(config_key=event.config_key, new_value=applied_value)
        )
