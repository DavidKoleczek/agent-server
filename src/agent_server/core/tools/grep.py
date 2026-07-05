from pathlib import Path
import subprocess
from typing import ClassVar

from liquid import render
from openai.types.responses.function_tool_param import FunctionToolParam
from pydantic import BaseModel

from agent_server.core.tools._ripgrep import find_ripgrep, run_ripgrep
from agent_server.core.tools._utils import ConstraintPolicy, ConstraintRule, check_path_constraint

TOOL_NAME = "grep"
RIPGREP_NOT_FOUND_ERROR = "Error: ripgrep execution failed, please use the shell to conduct the search"
RIPGREP_TIMEOUT_SECONDS = 30

TOOL_DESCRIPTION = """A powerful search tool built on ripgrep

Usage:
- ALWAYS use Grep for search tasks. NEVER invoke `grep` or `rg` as a Bash command. The Grep tool has been optimized for correct permissions and access.
- Supports full regex syntax (e.g., "log.*Error", "function\\s+\\w+")
- Filter files with glob parameter (e.g., "*.js", "**/*.tsx") or type parameter (e.g., "js", "py", "rust")
- Output modes: "content" shows matching lines, "files_with_matches" shows only file paths (default), "count" shows match counts
- Use Task tool for open-ended searches requiring multiple rounds
- Pattern syntax: Uses ripgrep (not grep) - literal braces need escaping (use `interface\\{\\}` to find `interface{}` in Go code)
- Multiline matching: By default patterns match within single lines only. For cross-line patterns like `struct \\{[\\s\\S]*?field`, use `multiline: true`"""

GREP_TOOL_DEFINITION: FunctionToolParam = {
    "type": "function",
    "name": TOOL_NAME,
    "description": TOOL_DESCRIPTION,
    "parameters": {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "The regular expression pattern to search for in file contents",
            },
            "path": {
                "type": ["string", "null"],
                "description": "File or directory to search in (rg PATH). Defaults to current working directory.",
            },
            "glob": {
                "type": ["string", "null"],
                "description": 'Glob pattern to filter files (e.g. "*.js", "*.{ts,tsx}") - maps to rg --glob',
            },
            "output_mode": {
                "type": ["string", "null"],
                "enum": ["content", "files_with_matches", "count"],
                "description": 'Output mode: "content" shows matching lines (supports -A/-B/-C context, -n line numbers, head_limit), "files_with_matches" shows file paths (supports head_limit), "count" shows match counts (supports head_limit). Defaults to "files_with_matches".',
            },
            "-B": {
                "type": ["number", "null"],
                "description": 'Number of lines to show before each match (rg -B). Requires output_mode: "content", ignored otherwise.',
            },
            "-A": {
                "type": ["number", "null"],
                "description": 'Number of lines to show after each match (rg -A). Requires output_mode: "content", ignored otherwise.',
            },
            "-C": {
                "type": ["number", "null"],
                "description": 'Number of lines to show before and after each match (rg -C). Requires output_mode: "content", ignored otherwise.',
            },
            "-n": {
                "type": ["boolean", "null"],
                "description": 'Show line numbers in output (rg -n). Requires output_mode: "content", ignored otherwise. Defaults to true.',
            },
            "-i": {"type": ["boolean", "null"], "description": "Case insensitive search (rg -i)"},
            "type": {
                "type": ["string", "null"],
                "description": "File type to search (rg --type). Common types: js, py, rust, go, java, etc. More efficient than include for standard file types.",
            },
            "head_limit": {
                "type": ["number", "null"],
                "description": 'Limit output to first N lines/entries, equivalent to "| head -N". Works across all output modes: content (limits output lines), files_with_matches (limits file paths), count (limits count entries). Defaults to 0 (unlimited).',
            },
            "offset": {
                "type": ["number", "null"],
                "description": 'Skip first N lines/entries before applying head_limit, equivalent to "| tail -n +N | head -N". Works across all output modes. Defaults to 0.',
            },
            "multiline": {
                "type": ["boolean", "null"],
                "description": "Enable multiline mode where . matches newlines and patterns can span lines (rg -U --multiline-dotall). Default: false.",
            },
        },
        "required": [
            "pattern",
            "path",
            "glob",
            "output_mode",
            "-B",
            "-A",
            "-C",
            "-n",
            "-i",
            "type",
            "head_limit",
            "offset",
            "multiline",
        ],
        "additionalProperties": False,
    },
    "strict": True,
}


class GrepConstraintRule(ConstraintRule):
    pass


class GrepToolConfig(BaseModel):
    working_dir: Path
    rules: list[GrepConstraintRule] = []
    default_policy: ConstraintPolicy = ConstraintPolicy.ASK


class GrepTool:
    TOOLS: ClassVar[dict[str, FunctionToolParam]] = {
        TOOL_NAME: GREP_TOOL_DEFINITION,
    }

    def __init__(self, config: GrepToolConfig) -> None:
        self.config = config

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

    def execute(self, **arguments: object) -> str:
        """Execute a ripgrep search.

        Assumes caller has already verified permission via check_constraint().

        Args:
            **arguments: Tool arguments containing:
                - pattern: Regex pattern to search for.
                - path: File or directory to search in (defaults to working_dir).
                - glob: Glob pattern to filter files.
                - output_mode: "content", "files_with_matches", or "count".
                - -B: Lines to show before matches.
                - -A: Lines to show after matches.
                - -C: Lines to show before and after matches.
                - -n: Show line numbers, defaults to True for content mode.
                - -i: Case-insensitive search.
                - type: File type filter.
                - head_limit: Limit output to first N lines/entries.
                - offset: Skip first N lines/entries.
                - multiline: Enable multiline matching.

        Returns:
            Search results or error message.
        """
        pattern = str(arguments.get("pattern", ""))
        path_arg = arguments.get("path")
        path = str(path_arg) if path_arg else None
        glob_arg = arguments.get("glob")
        glob = str(glob_arg) if glob_arg else None
        output_mode_arg = arguments.get("output_mode")
        output_mode = str(output_mode_arg) if output_mode_arg else None
        context_before_arg = arguments.get("-B")
        context_before = int(str(context_before_arg)) if context_before_arg is not None else None
        context_after_arg = arguments.get("-A")
        context_after = int(str(context_after_arg)) if context_after_arg is not None else None
        context_arg = arguments.get("-C")
        context = int(str(context_arg)) if context_arg is not None else None
        line_numbers_arg = arguments.get("-n")
        line_numbers = bool(line_numbers_arg) if line_numbers_arg is not None else None
        case_insensitive_arg = arguments.get("-i")
        case_insensitive = bool(case_insensitive_arg) if case_insensitive_arg is not None else None
        file_type_arg = arguments.get("type")
        file_type = str(file_type_arg) if file_type_arg else None
        head_limit_arg = arguments.get("head_limit")
        head_limit = int(str(head_limit_arg)) if head_limit_arg is not None else None
        offset_arg = arguments.get("offset")
        offset = int(str(offset_arg)) if offset_arg is not None else None
        multiline_arg = arguments.get("multiline")
        multiline = bool(multiline_arg) if multiline_arg is not None else None

        if find_ripgrep() is None:
            return RIPGREP_NOT_FOUND_ERROR

        args = self._build_args(
            pattern=pattern,
            path=path,
            glob=glob,
            output_mode=output_mode,
            context_before=context_before,
            context_after=context_after,
            context=context,
            line_numbers=line_numbers,
            case_insensitive=case_insensitive,
            file_type=file_type,
            multiline=multiline,
        )

        try:
            result = run_ripgrep(args, self.config.working_dir, timeout=RIPGREP_TIMEOUT_SECONDS)
        except FileNotFoundError:
            return RIPGREP_NOT_FOUND_ERROR
        except subprocess.TimeoutExpired:
            return render("Error: ripgrep search timed out after {{seconds}} seconds", seconds=RIPGREP_TIMEOUT_SECONDS)

        if result.returncode >= 2:
            return f"Error: ripgrep failed: {result.stderr.strip()}"

        output = result.stdout

        if offset or head_limit:
            lines = output.splitlines()
            start = offset or 0
            lines = lines[start : start + head_limit] if head_limit else lines[start:]
            output = "\n".join(lines)
            if lines:
                output += "\n"

        if not output.strip():
            return "No matches found"

        return output

    def _build_args(
        self,
        pattern: str,
        path: str | None,
        glob: str | None,
        output_mode: str | None,
        context_before: int | None,
        context_after: int | None,
        context: int | None,
        line_numbers: bool | None,
        case_insensitive: bool | None,
        file_type: str | None,
        multiline: bool | None,
    ) -> list[str]:
        """Build ripgrep command line arguments."""
        args: list[str] = []

        mode = output_mode or "files_with_matches"
        if mode == "files_with_matches":
            args.append("-l")
        elif mode == "count":
            args.append("-c")

        if mode == "content":
            if context is not None:
                args.extend(["-C", str(context)])
            else:
                if context_before is not None:
                    args.extend(["-B", str(context_before)])
                if context_after is not None:
                    args.extend(["-A", str(context_after)])

            show_line_numbers = line_numbers if line_numbers is not None else True
            if show_line_numbers:
                args.append("-n")

        if case_insensitive:
            args.append("-i")

        if multiline:
            args.extend(["-U", "--multiline-dotall"])

        if glob:
            args.extend(["--glob", glob])

        if file_type:
            args.extend(["--type", file_type])

        args.extend(["-e", pattern])

        search_path = path if path else str(self.config.working_dir)
        args.append(search_path)

        return args
