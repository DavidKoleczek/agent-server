from pathlib import Path
from typing import ClassVar

from openai.types.responses.function_tool_param import FunctionToolParam
from pydantic import BaseModel

from agent_server.core.tools._utils import ConstraintPolicy, ConstraintRule, check_path_constraint

TOOL_NAME = "glob"

TOOL_DESCRIPTION = """File pattern matching tool that works with any directory size
- Supports glob patterns like "**/*.js" or "src/**/*.ts"
- Returns matching file paths sorted by modification time
- Use this tool when you need to find files by name patterns
- When you are doing an open ended search that may require multiple rounds of globbing and grepping, use the Agent tool instead
- You can call multiple tools in a single response. It is always better to speculatively perform multiple searches in parallel if they are potentially useful."""

GLOB_TOOL_DEFINITION: FunctionToolParam = {
    "type": "function",
    "name": TOOL_NAME,
    "description": TOOL_DESCRIPTION,
    "parameters": {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "The glob pattern to match files against"},
            "path": {
                "type": ["string", "null"],
                "description": "The directory to search in. If not specified, the current working directory will be used. Must be a valid directory path if provided.",
            },
        },
        "required": ["pattern", "path"],
        "additionalProperties": False,
    },
    "strict": True,
}


class GlobConstraintRule(ConstraintRule):
    pass


class GlobToolConfig(BaseModel):
    working_dir: Path
    rules: list[GlobConstraintRule] = []
    default_policy: ConstraintPolicy = ConstraintPolicy.ASK


class GlobTool:
    TOOLS: ClassVar[dict[str, FunctionToolParam]] = {
        TOOL_NAME: GLOB_TOOL_DEFINITION,
    }

    def __init__(self, config: GlobToolConfig) -> None:
        self.config = config

    def execute(self, **arguments: object) -> str:
        """Execute a glob search for files matching a pattern.

        Assumes caller has already verified permission via check_constraint().

        Args:
            **arguments: Tool arguments containing:
                - pattern: Glob pattern to match files against.
                - path: Directory to search in (defaults to working_dir).

        Returns:
            Matching file paths sorted by modification time (most recent first),
            one per line, or "No matches found" if no files match.
        """
        pattern = str(arguments.get("pattern", ""))
        path_arg = arguments.get("path")
        search_dir = Path(str(path_arg)) if path_arg else self.config.working_dir

        if not search_dir.exists():
            return f"Error: Directory not found: {search_dir}"
        if not search_dir.is_dir():
            return f"Error: Path is not a directory: {search_dir}"

        matches = [p for p in search_dir.glob(pattern) if p.is_file()]

        if not matches:
            return "No matches found"

        matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)

        return "\n".join(str(p) for p in matches)

    def check_constraint(self, **arguments: object) -> ConstraintPolicy:
        """Determine what constraint applies to a given path.

        Rules are evaluated in order; first match wins.
        If no rule matches, default_policy is used.
        """
        path_arg = arguments.get("path")
        file_path = Path(str(path_arg)) if path_arg else self.config.working_dir
        return check_path_constraint(
            file_path,
            self.config.rules,
            self.config.working_dir,
            self.config.default_policy,
        )
