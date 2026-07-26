from abc import ABC, abstractmethod
from typing import Any

from openai.types.responses.function_tool_param import FunctionToolParam

from agent_server.core.tools._utils import ConstraintPolicy
from agent_server.schemas.activity import InputRequestActivity, InputRequestResponseEvent


class Tool(ABC):
    """Base class for tools exposed to the model."""

    @abstractmethod
    def get_tool_defs(self) -> dict[str, FunctionToolParam]: ...

    @abstractmethod
    def check_constraint(self, **arguments: Any) -> ConstraintPolicy: ...


class FunctionTool(Tool):
    """Tool whose function call can be executed immediately."""

    @abstractmethod
    def execute(self, **arguments: Any) -> Any: ...


class InputRequestTool(Tool):
    """Tool whose function call requires a response from the user."""

    @abstractmethod
    def create_request(self, call_id: str, **arguments: Any) -> InputRequestActivity: ...

    @abstractmethod
    def resolve_request(self, request: InputRequestActivity, event: InputRequestResponseEvent) -> str: ...
