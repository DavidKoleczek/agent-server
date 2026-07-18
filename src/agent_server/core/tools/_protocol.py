from typing import Any, Protocol, runtime_checkable

from openai.types.responses.function_tool_param import FunctionToolParam

from agent_server.core.tools._utils import ConstraintPolicy


@runtime_checkable
class Tool(Protocol):
    """Protocol that all tools must satisfy.

    Tools must have:
        - get_tool_defs: Method returning tool names mapped to their OpenAI function definitions
        - check_constraint: Method returning the constraint policy for given arguments
        - execute: Method executing the tool with given arguments (may be sync or async)
    """

    def get_tool_defs(self) -> dict[str, FunctionToolParam]: ...

    def check_constraint(self, **arguments: Any) -> ConstraintPolicy: ...

    def execute(self, **arguments: Any) -> Any: ...
