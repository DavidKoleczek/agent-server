"""
The replacement approaches in this edit tool are sourced from:
https://github.com/sst/opencode/blob/dev/packages/opencode/src/tool/edit.ts
"""

from collections.abc import Generator
from pathlib import Path
from typing import NamedTuple

from liquid import render
from loguru import logger
from openai.types.responses.function_tool_param import FunctionToolParam
from pydantic import BaseModel, Field

from agent_server.core.tools._utils import ConstraintPolicy, ConstraintRule, check_path_constraint

type Replacer = Generator[str]

# Error message templates (use liquid render() for templates with variables)
ERR_OLD_EQUALS_NEW = "Error: old_string and new_string must be different"
ERR_FILE_NOT_FOUND = "Error: File {{ path }} not found"
ERR_PATH_IS_DIRECTORY = "Error: Path is a directory, not a file: {{ path }}"
ERR_NOT_FOUND = "Error: old_string not found in file"
ERR_MULTIPLE_MATCHES = "Error: Found multiple matches for old_string. Provide more surrounding context to identify the correct match or call with replace_all as true."

TOOL_NAME = "edit"

TOOL_DESCRIPTION = """"Performs exact string replacements in files. 

Usage:
- You must use your `Read` tool at least once in the conversation before editing. This tool will error if you attempt an edit without reading the file. 
- When editing text from Read tool output, ensure you preserve the exact indentation (tabs/spaces) as it appears AFTER the line number prefix. \
The line number prefix format is: spaces + line number + tab. Everything after that tab is the actual file content to match. Never include any part of the line number prefix in the old_string or new_string.
- ALWAYS prefer editing existing files in the codebase. NEVER write new files unless explicitly required.
- Only use emojis if the user explicitly requests it. Avoid adding emojis to files unless asked.
- Special case: If old_string is empty, the entire file is replaced with new_string.
- The edit will FAIL if `old_string` is not unique in the file. Either provide a larger string with more surrounding context to make it unique or use `replace_all` to change every instance of `old_string`. 
- Use `replace_all` for replacing and renaming strings across the file. This parameter is useful if you want to rename a variable for instance."""

EDIT_TOOL_DEFINITION: FunctionToolParam = {
    "type": "function",
    "name": TOOL_NAME,
    "description": TOOL_DESCRIPTION,
    "parameters": {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "The absolute path to the file to modify"},
            "old_string": {"type": "string", "description": "The text to replace"},
            "new_string": {
                "type": "string",
                "description": "The text to replace it with (must be different from old_string)",
            },
            "replace_all": {
                "type": "boolean",
                "default": False,
                "description": "Replace all occurrences of old_string (default false)",
            },
        },
        "required": ["file_path", "old_string", "new_string", "replace_all"],
        "additionalProperties": False,
    },
    "strict": True,
}


class EditConstraintRule(ConstraintRule):
    pass


class EditToolConfig(BaseModel):
    working_dir: Path
    rules: list[EditConstraintRule] = []
    default_policy: ConstraintPolicy = ConstraintPolicy.ASK
    context_lines: int = Field(default=4, description="Number of lines to show before/after changes in output snippet.")
    err_old_equals_new: str = Field(
        default=ERR_OLD_EQUALS_NEW,
        description="Error when old_string equals new_string. Plain string, no variables.",
    )
    err_file_not_found: str = Field(
        default=ERR_FILE_NOT_FOUND,
        description="Error when file does not exist. Liquid template with variable: {{ path }}.",
    )
    err_path_is_directory: str = Field(
        default=ERR_PATH_IS_DIRECTORY,
        description="Error when path is a directory. Liquid template with variable: {{ path }}.",
    )
    err_not_found: str = Field(
        default=ERR_NOT_FOUND,
        description="Error when old_string is not found in file. Plain string, no variables.",
    )
    err_multiple_matches: str = Field(
        default=ERR_MULTIPLE_MATCHES,
        description="Error when multiple matches found without replace_all. Plain string, no variables.",
    )


class EditTool:
    def __init__(self, config: EditToolConfig) -> None:
        self.config = config

    def get_tool_defs(self) -> dict[str, FunctionToolParam]:
        return {TOOL_NAME: EDIT_TOOL_DEFINITION}

    def execute(self, **arguments: object) -> str:
        """Edit a file by replacing old_string with new_string.

        Assumes caller has already verified permission via check_constraint().

        Args:
            **arguments: Tool arguments containing:
                - file_path: Absolute path to the file to edit.
                - old_string: The text to replace. If empty, creates/overwrites the file.
                - new_string: The replacement text.
                - replace_all: If True, replace all occurrences.

        Returns:
            Success message with snippet of changes, or error message.
        """
        file_path = str(arguments.get("file_path", ""))
        old_string = str(arguments.get("old_string", ""))
        new_string = str(arguments.get("new_string", ""))
        replace_all = bool(arguments.get("replace_all", False))

        if old_string == new_string:
            return self.config.err_old_equals_new

        path = Path(file_path).resolve()

        # Empty old_string means create/overwrite mode
        if old_string == "":
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(new_string, encoding="utf-8")
                snippet = _format_snippet(new_string, [], self.config.context_lines)
                return f"The file {path} has been updated. Here's the result of running `cat -n` on a snippet of the edited file:\n{snippet}"
            except Exception as e:
                return f"Error writing file: {e}"

        # Edit mode: validate file exists
        if not path.exists():
            return render(self.config.err_file_not_found, path=path)

        if path.is_dir():
            return render(self.config.err_path_is_directory, path=path)

        try:
            content = path.read_text(encoding="utf-8")
            content = _normalize_line_endings(content)

            result = _replace(content, old_string, new_string, replace_all=replace_all)

            path.write_text(result.new_content, encoding="utf-8")
            snippet = _format_snippet(result.new_content, result.change_regions, self.config.context_lines)
            return f"The file {path} has been updated. Here's the result of running `cat -n` on a snippet of the edited file:\n{snippet}"
        except EditError as e:
            if e.error_type == EditErrorType.NOT_FOUND:
                return self.config.err_not_found
            return self.config.err_multiple_matches
        except Exception as e:
            return f"Error editing file: {e}"

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


class ReplaceResult(NamedTuple):
    """Result of a replacement operation."""

    new_content: str
    change_regions: list[tuple[int, int]]  # (start_line, end_line) tuples, 0-indexed


def _normalize_line_endings(text: str) -> str:
    """Normalize CRLF to LF."""
    return text.replace("\r\n", "\n")


def _simple_replacer(find: str) -> Replacer:
    """Yield the search string as-is for exact matching."""
    yield find


def _line_trimmed_replacer(content: str, find: str) -> Replacer:
    """Yield matches where lines match after trimming whitespace.

    Compares lines after stripping leading/trailing whitespace.
    Yields the actual content (preserving original whitespace) when trimmed versions match.
    """
    content_lines = content.split("\n")
    search_lines = find.split("\n")

    # Remove trailing empty line if present (common when find ends with newline)
    if search_lines and search_lines[-1] == "":
        search_lines.pop()

    if not search_lines:
        return

    for i in range(len(content_lines) - len(search_lines) + 1):
        matches = True
        for j, search_line in enumerate(search_lines):
            if content_lines[i + j].strip() != search_line.strip():
                matches = False
                break

        if matches:
            # Calculate the actual substring position
            start_idx = sum(len(content_lines[k]) + 1 for k in range(i))
            end_idx = start_idx
            for k in range(len(search_lines)):
                end_idx += len(content_lines[i + k])
                if k < len(search_lines) - 1:
                    end_idx += 1  # newline between lines

            yield content[start_idx:end_idx]


class EditErrorType:
    """Error types for edit operations."""

    NOT_FOUND = "not_found"
    MULTIPLE_MATCHES = "multiple_matches"


class EditError(Exception):
    """Error during edit operation."""

    def __init__(self, error_type: str) -> None:
        self.error_type = error_type
        super().__init__(error_type)


def _replace(content: str, old_string: str, new_string: str, *, replace_all: bool = False) -> ReplaceResult:
    """Replace old_string with new_string in content.

    Tries replacer strategies in order from strict to fuzzy.

    Args:
        content: The file content to modify.
        old_string: The string to find and replace.
        new_string: The replacement string.
        replace_all: If True, replace all occurrences. If False, require unique match.

    Returns:
        ReplaceResult with new content and change regions.

    Raises:
        EditError: If old_string not found or multiple matches found (when replace_all=False).
    """

    not_found = True

    replacers = [
        ("simple", _simple_replacer(old_string)),
        ("line_trimmed", _line_trimmed_replacer(content, old_string)),
    ]

    for replacer_name, replacer in replacers:
        for search in replacer:
            index = content.find(search)
            if index == -1:
                continue

            not_found = False

            if replacer_name != "simple":
                logger.info("Edit tool matched using {} replacer", replacer_name)

            if replace_all:
                # Find all occurrences and their positions
                change_regions: list[tuple[int, int]] = []
                new_content = ""
                last_end = 0
                search_start = 0

                while True:
                    idx = content.find(search, search_start)
                    if idx == -1:
                        break

                    # Calculate line numbers for this occurrence
                    start_line = content[:idx].count("\n")
                    end_line = start_line + new_string.count("\n")
                    change_regions.append((start_line, end_line))

                    new_content += content[last_end:idx] + new_string
                    last_end = idx + len(search)
                    search_start = idx + len(search)

                new_content += content[last_end:]
                return ReplaceResult(new_content, change_regions)

            # Single replacement mode: ensure match is unique
            last_index = content.rfind(search)
            if index != last_index:
                continue  # Multiple matches, try next replacer

            # Found unique match
            start_line = content[:index].count("\n")
            end_line = start_line + new_string.count("\n")
            new_content = content[:index] + new_string + content[index + len(search) :]
            return ReplaceResult(new_content, [(start_line, end_line)])

    if not_found:
        raise EditError(EditErrorType.NOT_FOUND)

    raise EditError(EditErrorType.MULTIPLE_MATCHES)


def _format_snippet(content: str, change_regions: list[tuple[int, int]], context_lines: int) -> str:
    """Format a snippet of content around change regions in cat -n style.

    Args:
        content: The file content.
        change_regions: List of (start_line, end_line) tuples (0-indexed).
        context_lines: Number of lines to show before and after each region.

    Returns:
        Formatted snippet with line numbers.
    """
    lines = content.split("\n")
    total_lines = len(lines)

    if not change_regions:
        # Show entire file if no specific regions
        regions_to_show = [(0, total_lines - 1)]
    else:
        # Expand each region with context and merge overlapping regions
        expanded: list[tuple[int, int]] = []
        for start, end in sorted(change_regions):
            region_start = max(0, start - context_lines)
            region_end = min(total_lines - 1, end + context_lines)

            if expanded and region_start <= expanded[-1][1] + 1:
                # Merge with previous region
                expanded[-1] = (expanded[-1][0], region_end)
            else:
                expanded.append((region_start, region_end))

        regions_to_show = expanded

    # Format output
    output_parts: list[str] = []
    for region_start, region_end in regions_to_show:
        for line_num in range(region_start, region_end + 1):
            # cat -n format: 6-character right-aligned line number, tab, content
            output_parts.append(f"{line_num + 1:>6}\t{lines[line_num]}")

    return "\n".join(output_parts)
