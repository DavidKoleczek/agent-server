from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal
import uuid

from interop_router.types import ChatMessage, SupportedModel
from openai.types.shared import Reasoning
from pydantic import BaseModel, Field

from agent_server.core.tools._protocol import Tool


@dataclass
class AgentChatHistory(ChatMessage):
    """Chat message with subagent tracking for nested agent calls."""

    subagents: list[AgentChatHistory] = field(default_factory=list)


class BaseAgentEvent(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    agent_id: str = Field(default="main", description="Identifier for the agent that produced this event")
    agent_name: str = Field(default="main", description="Friendly name of the agent that produced this event")


class AgentMessageEvent(BaseAgentEvent):
    message: str
    type: Literal["message"] = "message"


class AgentReasoningEvent(BaseAgentEvent):
    message: str
    type: Literal["reasoning"] = "reasoning"


class AgentToolCallEvent(BaseAgentEvent):
    arguments: str
    call_id: str
    name: str
    needs_approval: bool = True
    type: Literal["function_call"] = "function_call"


class AgentToolOutputEvent(BaseAgentEvent):
    call_id: str
    name: str
    output: dict[str, Any]
    type: Literal["function_call_output"] = "function_call_output"


class AgentTurnEnd(BaseAgentEvent):
    reason: Literal["tools_need_decision", "completed"]
    type: Literal["turn_end"] = "turn_end"


AgentEvent = AgentMessageEvent | AgentReasoningEvent | AgentToolCallEvent | AgentToolOutputEvent | AgentTurnEnd


class UserMessageEvent(BaseModel):
    message: str
    type: Literal["message"] = "message"


class UserToolCallPermissionEvent(BaseModel):
    call_id: str
    permission: Literal["accept", "deny"]
    # Optional feedback from the user regarding their decision (can be set for either accept or deny and it will generate a new user message)
    feedback: str | None = None
    type: Literal["tool_call_permission"] = "tool_call_permission"


UserEvent = UserMessageEvent | UserToolCallPermissionEvent


class AgentConfig(BaseModel):
    working_dir: Path = Field(description="Directory the agent is working in.")
    chat_file: Path | None = Field(
        default=None,
        description="Path to the chat history. If None, a new file is created in the system temp directory.",
    )
    max_subagent_depth: int = Field(
        default=1,
        description="Maximum recursion depth for sub-agents spawned via the Task Tool. 1 means only the main agent can create sub-agents.",
    )


class SubagentConfig(BaseModel):
    name: str = Field(description="Identifier for this agent type (e.g., 'general-purpose')")
    description: str = Field(description="Description of this agent type for the tool definition")
    turn_config: TurnConfig = Field(description="Turn configuration for this subagent type")


class TurnConfig(BaseModel):
    model_config = {"arbitrary_types_allowed": True}

    model: SupportedModel = Field(description="Model ID for API calls")
    model_friendly_name: str = Field(description="Human-readable model name")
    model_knowledge_cutoff: str = Field(description="Model knowledge cutoff date")
    timezone: str = Field(default="UTC", description="IANA timezone name for date display")
    tools: list[Tool] = Field(default_factory=list, description="List of tools available to the agent during this turn")
    subagents: list[SubagentConfig] = Field(
        default_factory=list, description="Available subagent types with their names, descriptions, and configs"
    )
    reasoning: Reasoning = Field(
        default_factory=lambda: Reasoning(effort="medium", summary="auto"),
        description="Reasoning configuration for compatible models",
    )
    enable_built_in_web_tool: bool = Field(
        default=False, description="Whether to enable the built-in web browsing tool"
    )
    system_prompt_template: str = Field(description="Liquid template for the system prompt")
