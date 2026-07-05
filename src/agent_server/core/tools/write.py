from pathlib import Path
from typing import ClassVar

from openai.types.responses.function_tool_param import FunctionToolParam
from pydantic import BaseModel

from agent_server.core.tools._utils import ConstraintPolicy, ConstraintRule, check_path_constraint

TOOL_NAME = "write"

TOOL_DESCRIPTION = """Writes a file to the local filesystem.

Usage:
- This tool will overwrite the existing file if there is one at the provided path.
- If this is an existing file, you MUST use the Read tool first to read the file's contents. This tool will fail if you did not read the file first.
- ALWAYS prefer editing existing files in the codebase. NEVER write new files unless explicitly required.
- NEVER proactively create documentation files (*.md) or README files. Only create documentation files if explicitly requested by the User.
- Only use emojis if the user explicitly requests it. Avoid writing emojis to files unless asked."""

WRITE_TOOL_DEFINITION: FunctionToolParam = {
    "type": "function",
    "name": TOOL_NAME,
    "description": TOOL_DESCRIPTION,
    "parameters": {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "The absolute path to the file to write (must be absolute, not relative)",
            },
            "content": {"type": "string", "description": "The content to write to the file"},
        },
        "required": ["file_path", "content"],
        "additionalProperties": False,
    },
    "strict": True,
}


class WriteConstraintRule(ConstraintRule):
    pass


class WriteToolConfig(BaseModel):
    working_dir: Path
    rules: list[WriteConstraintRule] = []
    default_policy: ConstraintPolicy = ConstraintPolicy.ASK


class WriteTool:
    TOOLS: ClassVar[dict[str, FunctionToolParam]] = {
        TOOL_NAME: WRITE_TOOL_DEFINITION,
    }

    def __init__(self, config: WriteToolConfig) -> None:
        self.config = config

    def execute(self, **arguments: object) -> str:
        """Write content to a file.

        Assumes caller has already verified permission via check_constraint().

        Args:
            **arguments: Tool arguments containing:
                - file_path: Absolute path to the file to write.
                - content: The content to write to the file.

        Returns:
            Success message or error string.
        """
        file_path = str(arguments.get("file_path", ""))
        content = str(arguments.get("content", ""))
        path = Path(file_path)

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            return f"File created successfully at: {file_path}"
        except Exception as e:
            return f"Unknown internal error writing file: {e}"

    def check_constraint(self, **arguments: object) -> ConstraintPolicy:
        """Determine what constraint applies to a given path.

        Rules are evaluated in order; first match wins.
        If no rule matches, default_policy is used.
        """
        file_path = Path(str(arguments.get("file_path", "")))
        return check_path_constraint(
            file_path,
            self.config.rules,
            self.config.working_dir,
            self.config.default_policy,
        )
