import asyncio
import copy
from datetime import datetime
import inspect
import json
import os
from pathlib import Path
import re
import uuid

from anthropic import AsyncAnthropic
from google import genai
from interop_router.router import Router
from interop_router.types import ChatMessage, InteropRouterError, RouterResponse, RouterStream, SupportedModel
from liquid import render
from openai import AsyncOpenAI
from openai.types.responses import EasyInputMessageParam
from openai.types.responses.response_input_item_param import FunctionCallOutput
from openai.types.responses.tool_param import ToolParam
from openai.types.shared_params import Reasoning
from pydantic import BaseModel, Field, ValidationError

from agent_server.agent.activity_converter import response_to_activities
from agent_server.agent.activity_stream_converter import ActivityStreamConverter, error_event, is_terminal_error
from agent_server.agent.prompts.system_prompt import SYSTEM_PROMPT
from agent_server.agent.prompts.tool import TOOL_AUTO_DENIED, TOOL_USER_DENIED
from agent_server.core.hooks import git, system_info
from agent_server.core.tools._protocol import Tool
from agent_server.core.tools._utils import ConstraintPolicy
from agent_server.core.tools.presets import permissive_tools, standard_tools
from agent_server.schemas.activity import (
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
from agent_server.schemas.session import SessionChatMessage, SessionConfig
from agent_server.storage.session_store import SessionStore


class AgentConfig(BaseModel):
    working_dir: Path = Field(description="Directory the agent is working in.")
    session_database: Path | None = Field(
        default=None,
        description="Path to the SQLite session database. If None, a new database is created in .agents/sessions.",
    )
    max_subagent_depth: int = Field(
        default=1,
        description="Maximum recursion depth for sub-agents spawned via the Task Tool. 1 means only the main agent can create sub-agents.",
    )


class Agent:
    def __init__(
        self,
        config: AgentConfig,
        client_events: asyncio.Queue[ClientEvent],
        streaming_events: asyncio.Queue[StreamingEvent],
    ):
        self.config = config
        self.client_events = client_events
        self.streaming_events = streaming_events

        self._session_database = self._resolve_session_database()
        self._session_store = SessionStore(self._session_database)
        self.history: list[SessionChatMessage] = self._session_store.load_session_chat_messages()
        self.activities: list[SessionActivity] = self._session_store.load_activities()
        self.session_config: SessionConfig = self._session_store.load_session_config()

        # TODO: Temp init the router here
        self.router = Router()
        self.router.register("openai", AsyncOpenAI())
        self.router.register("gemini", genai.Client(api_key=os.getenv("GEMINI_API_KEY")))
        self.router.register("anthropic", AsyncAnthropic())

        self.model: SupportedModel = self.session_config.model

        self._apply_tool_preset(self.session_config.tool_preset)

    async def start(self) -> None:
        """Kicks off the agent and will never return.
        Cancel or Quit should be handled by caller by killing this. We will guarantee that we can gracefully recover from that.
        """

        while True:
            self.streaming_events.put_nowait(StatusEvent(status_id="agent_running"))
            # Block until there is a client event to process
            client_event = await self.client_events.get()
            if isinstance(client_event, UserMessageEvent):
                msg = ChatMessage(message=EasyInputMessageParam(role="user", content=client_event.content))
                self._append_chat_message(msg)
                activity = UserActivity(id=str(uuid.uuid4()), state="complete", content=client_event.content)
                self._append_activity(activity)
                await self.run()
            elif isinstance(client_event, PermissionChangeEvent):
                # Handle the tool call again based on the new permission by getting the associated tool call and then executing it.
                tool_call_msg = self._get_function_call_by_id(client_event.id)
                if tool_call_msg:
                    await self._execute_tool_call(message=tool_call_msg, permission=client_event.permission)
                    await self.run()
            elif isinstance(client_event, SessionConfigChangeEvent):
                self._handle_config_change(client_event)

    async def run(self) -> None:
        """
        Handles agent turn. It interacts with the outside world by reading and writing to user_activities and agent_activities.
        """

        while True:
            self.streaming_events.put_nowait(StatusEvent(status_id="starting_new_turn"))

            # At the start of each iteration, add any user activities that have come in.
            await self._drain_client_events(self.client_events)

            # If there's any pending tool calls, break and wait until the user approves/denies.
            if any(isinstance(a, TaskActivity) and a.permission == "pending" for a in self.activities):
                break

            # Get system prompt ready
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
                break

            # Update the activites based on the new response.
            [self._append_activity(activity) for activity in response_to_activities(response)]

            had_tool_call = False
            for msg in response.output:
                self._append_chat_message(msg)
                # For any non-tool call messages, add them to the history
                if msg.message.get("type") != "function_call":
                    continue

                await self._execute_tool_call(msg)
                had_tool_call = True

            # Logic determining if we should break out of the current agent loop and wait for more user input.
            break_loop = True
            # If there were tool calls, we continue the loop so the model can process their results.
            if had_tool_call:
                break_loop = False

            # If new user activities came in during the LLM call or tool call, like a message or tool approval, we process those right away.
            new_activities = await self._drain_client_events(self.client_events)
            if new_activities:
                break_loop = False

            # However, if there are any pending tool calls, we can't do anything until its approved or denied so we break.
            # Check all activities for any remaining pending tool calls.
            pending_tool_calls = []
            for activity in self.activities:
                if isinstance(activity, TaskActivity) and activity.permission == "pending":
                    pending_tool_calls.append(activity)
            if pending_tool_calls:
                break_loop = True

            if break_loop:
                break

        self.streaming_events.put_nowait(StatusEvent(status_id="agent_turn_ended"))

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

    def _append_chat_message(
        self, message: ChatMessage, permission: TaskPermission | None = None
    ) -> SessionChatMessage:
        """Processes the given ChatMessage into the session store and in-memory history."""
        position = len(self.history)
        self._session_store.add_chat_message(position, message, permission=permission)
        session_message = SessionChatMessage(position=position, permission=permission, chat_message=message)
        self.history.append(session_message)
        return session_message

    def _append_activity(self, activity: SessionActivity) -> SessionActivity:
        """Processes the given activity into the session store and in-memory activities"""
        position = len(self.activities)
        self._session_store.save_activity(position, activity)
        self.activities.append(activity)
        return activity

    async def _drain_client_events(self, queue: asyncio.Queue[ClientEvent]) -> list[SessionChatMessage]:
        """Handle all the client events that have come in since the last time we checked.
        Non-blocking: only takes items that are already available.

        Returns:
            The session chat messages that were appended to history.
        """
        messages: list[SessionChatMessage] = []
        while True:
            try:
                event = queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if isinstance(event, UserMessageEvent):
                msg = ChatMessage(message=EasyInputMessageParam(role="user", content=event.content))
                messages.append(self._append_chat_message(msg))
                activity = UserActivity(id=str(uuid.uuid4()), state="complete", content=event.content)
                self._append_activity(activity)
            elif isinstance(event, PermissionChangeEvent):
                tool_call_msg = self._get_function_call_by_id(event.id)
                if tool_call_msg:
                    await self._execute_tool_call(message=tool_call_msg, permission=event.permission)

                # SessionChatMessages are not updated here because the FunctionCallOutput should not exist yet to update its permission.
            elif isinstance(event, SessionConfigChangeEvent):
                self._handle_config_change(event)
        return messages

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

    async def _execute_tool_call(self, message: ChatMessage, permission: TaskPermission | None = None) -> None:
        # Get the call id and return if not present
        call_id = message.message.get("call_id")
        if not isinstance(call_id, str) or not call_id:
            return

        # Check if there is already a function_call_output for this call_id in the history.
        already_resolved = any(
            m.chat_message.message.get("type") == "function_call_output"
            and m.chat_message.message.get("call_id") == call_id
            for m in self.history
        )
        if already_resolved:
            return

        arguments = json.loads(str(message.message.get("arguments", "{}")))
        tool_name = str(message.message.get("name", ""))
        tool = self._tools_by_name.get(tool_name)

        task_activity = next((a for a in self.activities if isinstance(a, TaskActivity) and a.id == call_id), None)
        if task_activity is None:
            return

        tool_output = None
        if tool and task_activity:
            if permission == "accepted":
                tool_policy = ConstraintPolicy.ALLOW
            elif permission == "denied":
                tool_policy = ConstraintPolicy.DENY
            elif permission == "pending":
                tool_policy = ConstraintPolicy.ASK
            else:
                tool_policy = tool.check_constraint(**arguments)
            # Handling the tool call given the current policy forthis tool
            if tool_policy == ConstraintPolicy.DENY:
                # Case where the tool call is automatically denied.
                tool_output = TOOL_USER_DENIED if permission else TOOL_AUTO_DENIED
                task_activity.state = "complete"
                task_activity.permission = "denied"
                task_activity.result = tool_output
            elif tool_policy == ConstraintPolicy.ASK:
                # Case where the tool call requires user approval.
                task_activity.state = "in_progress"
                task_activity.permission = "pending"
            elif tool_policy == ConstraintPolicy.ALLOW:
                # Case where the tool call is automatically allowed.
                task_activity.permission = "accepted"
                # Immediately let the client know that the tool is auto accepted and will be executed.
                task_updated_event = ActivityUpdatedEvent(activity=task_activity)
                self._session_store.update_activity(task_activity)
                self.streaming_events.put_nowait(task_updated_event)

                # Execute the tool
                self.streaming_events.put_nowait(StatusEvent(status_id="executing_tool"))
                raw_tool_output = tool.execute(**arguments)
                if inspect.iscoroutine(raw_tool_output):
                    raw_tool_output = await raw_tool_output
                if isinstance(raw_tool_output, str):
                    tool_output = raw_tool_output
                elif isinstance(raw_tool_output, list):
                    tool_output = json.dumps(raw_tool_output)
                else:
                    tool_output = str(raw_tool_output)

                task_activity.state = "complete"
                task_activity.result = tool_output

            # Add the tool output to the history
            if tool_output and task_activity.permission != "pending":
                output_message = ChatMessage(
                    message=FunctionCallOutput(call_id=call_id, type="function_call_output", output=tool_output)
                )
                self._append_chat_message(output_message, permission=task_activity.permission)

            # Update the task_activity to reflect the new state
            self._session_store.update_activity(task_activity)
            task_updated_event = ActivityUpdatedEvent(activity=task_activity)
            self.streaming_events.put_nowait(task_updated_event)

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

    def _apply_tool_preset(self, tool_preset: str) -> None:
        """Rebuild the active tool set and its request/lookup indices for the given preset."""
        tools = []
        if tool_preset == "permissive":
            tools = permissive_tools(self.config.working_dir)
        elif tool_preset == "standard":
            tools = standard_tools(self.config.working_dir)
        self.tools = tools
        self._request_tools: list[ToolParam] = [defn for tool in self.tools for defn in tool.TOOLS.values()]
        self._tools_by_name: dict[str, Tool] = {name: tool for tool in self.tools for name in tool.TOOLS}
