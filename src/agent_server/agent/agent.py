"""
The agent needs to be initialized such that it can call itself in a sub-task.
We need to pay attention to the code paths that will be taken when its called recursively.
It needs to be able to recieve messages mid-task and inject those into the history so they get picked up the next iteration of the agent loop (they don't get added to sub-agents).

Required Features:
- First class Windows support
- Skills tool: .github/skills, .claude/skills, or .agents/skills
- Multiple models, with seamless swap in the middle of the conversation
- Defining subagents, which can be run in parallel
  - Ability to choose what context is passed (just from caller, last x messages, full history)
- Bash tool that handles background tasks well
  - General system for spawning background tasks that live in the context
  - If something is running the background, you can come back to it and it keeps streaming and updating its spot in the history and might even send a message when its done. Sub-agents can behave similarly
- Considers user activities (messages) that comes in right after the current LLM call (steering)
- Computer use mode
- Conversations as files, knows how to explore
- Bypass permissions is the default mode, with smart restrictions
- Infinite chat by default
- Good plan mode
  - Summarizes plan, but gives a link to the full plan
  - Critique of plans with other model
- Verifier "mode" - define criteria, and it iterates until its done
- Ability to use ! to send commands
- Teacher mode - does not implement, but instead explains.
- Built in file type handling for the read tool: pdf, docx, excel, etc
- Continual chat title refinement
- Integration with different apps (like Fusion) - App interaction protocol
- VSCode integration
- /messages - Dumps the current state/history that would be used for the next message being sent to the model into a temp file that is linked.
"""

import asyncio
import copy
from datetime import datetime
import inspect
import json
import os
from pathlib import Path
import re
import uuid

from agent_core.hooks import git, system_info
from agent_core.tools._protocol import Tool
from agent_core.tools.presets import permissive_tools
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
from pydantic import BaseModel, Field

from agent_server.agent.activity_converter import response_to_activities
from agent_server.agent.activity_stream_converter import ActivityStreamConverter, error_event, is_terminal_error
from agent_server.agent.prompts.system_prompt import SYSTEM_PROMPT
from agent_server.schemas.activity import (
    ClientEvent,
    ReadyEvent,
    SessionActivity,
    StreamingEvent,
    TaskPermission,
    TurnEndEvent,
    TurnStartEvent,
    UserMessageEvent,
)
from agent_server.schemas.session import SessionChatMessage
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
    def __init__(self, config: AgentConfig):
        self.config = config

        self._session_database = self._resolve_session_database()
        self._session_store = SessionStore(self._session_database)
        self.history: list[SessionChatMessage] = self._session_store.load_session_chat_messages()
        self.activities: list[SessionActivity] = self._session_store.load_activities()

        # TODO: Temp init the router here
        self.router = Router()
        self.router.register("openai", AsyncOpenAI())
        self.router.register("gemini", genai.Client(api_key=os.getenv("GEMINI_API_KEY")))
        self.router.register("anthropic", AsyncAnthropic())

        # TODO: Set model here
        self.model: SupportedModel = "gpt-5.5"

        self.tools = permissive_tools(self.config.working_dir)

    async def run(
        self, user_activities: asyncio.Queue[ClientEvent], agent_activities: asyncio.Queue[StreamingEvent]
    ) -> None:
        """
        Handles all the AI agent logic. It interacts with the outside world by ready and writing to user_activities and agent_activities, respectively.
        """
        agent_activities.put_nowait(ReadyEvent())
        agent_activities.put_nowait(TurnStartEvent())

        self._drain_user_activities(user_activities)
        if not self.history:
            return

        while True:
            # Get tools ready
            request_tools: list[ToolParam] = [defn for tool in self.tools for defn in tool.TOOLS.values()]
            tool_by_name: dict[str, Tool] = {}
            for tool in self.tools:
                for name in tool.TOOLS:
                    tool_by_name[name] = tool

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
                tools=request_tools,
                max_output_tokens=120_000,
            )
            response = await self._handle_router_stream(stream, agent_activities)
            if response is None:
                break

            [self._append_activity(activity) for activity in response_to_activities(response)]

            had_tool_call = False
            for msg in response.output:
                self._append_chat_message(msg)
                # For any non-tool call messages, add them to the history
                if msg.message.get("type") != "function_call":
                    continue

                # Handle tool calls by executing them and adding them to the history.
                # TODO: Check permissions
                call_id = str(msg.message.get("call_id", ""))
                arguments = json.loads(str(msg.message.get("arguments", "{}")))

                output = "Default output. This is indicative of an unknown error in executing the tool."
                tool_name = str(msg.message.get("name", ""))
                tool = tool_by_name.get(tool_name)
                if tool:
                    output = tool.execute(**arguments)
                    if inspect.iscoroutine(output):
                        output = await output
                    elif isinstance(output, list):
                        output = json.dumps(output)

                output_message = ChatMessage(
                    message=FunctionCallOutput(call_id=call_id, type="function_call_output", output=output)
                )
                self._append_chat_message(output_message)
                had_tool_call = True

            can_break = True
            if had_tool_call:
                can_break = False

            # TODO: there is a chance here that we get new activities between this check and when this returns.
            # We need some sort of lock to say we are not processing new activities right now and a new agent should be started.
            new_activities = self._drain_user_activities(user_activities)
            if new_activities:
                can_break = False

            if can_break:
                break

        agent_activities.put_nowait(TurnEndEvent())

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

        date_str = datetime.now().strftime("%Y-%m-%d")
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

    def _drain_user_activities(self, queue: asyncio.Queue[ClientEvent]) -> list[SessionChatMessage]:
        """Drain currently queued user message events into session chat history.
        Non-blocking: only takes items that are already available.

        Returns:
            The session chat messages that were appended to history.
        """
        messages: list[SessionChatMessage] = []
        while True:
            try:
                activity = queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if isinstance(activity, UserMessageEvent):
                msg = ChatMessage(message=EasyInputMessageParam(role="user", content=activity.content))
                messages.append(self._append_chat_message(msg))
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
