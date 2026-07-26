import base64
import os
from pathlib import Path

os.environ["PYMUPDF_SUGGEST_LAYOUT_ANALYZER"] = "0"


from openai.types.responses import ResponseFunctionCallOutputItemListParam
from openai.types.responses.function_tool_param import FunctionToolParam
from openai.types.responses.response_input_image_content_param import ResponseInputImageContentParam
from pydantic import BaseModel
import pymupdf4llm

from agent_server.core.tools._protocol import FunctionTool
from agent_server.core.tools._utils import ConstraintPolicy, ConstraintRule, check_path_constraint

TOOL_NAME = "read"

TOOL_DESCRIPTION = """Reads a file from the local filesystem. You can access any file directly by using this tool.
Assume this tool is able to read all files on the machine. If the User provides a path to a file assume that path is valid. It is okay to read a file that does not exist; an error will be returned.

Usage:
- The file_path parameter must be an absolute path, not a relative path
- By default, it reads up to 2000 lines starting from the beginning of the file
- You can optionally specify a line offset and limit (especially handy for long files), but it's recommended to read the whole file by not providing these parameters.
  - An offset of 0 will start reading from the beginning of the file.
- Any lines longer than 2000 characters will be truncated
- Results are returned using cat -n format, with line numbers starting at 1
- This tool allows you to read images (eg PNG, JPG, etc). When reading an image file the contents are presented visually as you are a multimodal LLM.
- This tool can read PDF files (.pdf). PDFs are processed page by page, extracting both text and visual content for analysis.
- This tool can only read files, not directories. To read a directory, use an ls command via the Bash tool.
- You can call multiple tools in a single response. It is always better to speculatively read multiple potentially useful files in parallel.
- You will regularly be asked to read screenshots. If the user provides a path to a screenshot, ALWAYS use this tool to view the file at the path. This tool will work with all temporary file paths.
- If you read a file that exists but has empty contents you will receive a system reminder warning in place of file contents."""

READ_TOOL_DEFINITION: FunctionToolParam = {
    "type": "function",
    "name": TOOL_NAME,
    "description": TOOL_DESCRIPTION,
    "parameters": {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "The absolute path to the file to read"},
            "offset": {
                "type": ["number", "null"],
                "description": "The line number to start reading from. Only provide if the file is too large to read at once. An offset of 0 will start reading from the beginning of the file.",
            },
            "limit": {
                "type": ["number", "null"],
                "description": "The number of lines to read. Only provide if the file is too large to read at once.",
            },
        },
        "required": ["file_path", "offset", "limit"],
        "additionalProperties": False,
    },
    "strict": True,
}


class ReadConstraintRule(ConstraintRule):
    pass


class ReadToolConfig(BaseModel):
    working_dir: Path
    rules: list[ReadConstraintRule] = []
    default_policy: ConstraintPolicy = ConstraintPolicy.ASK


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
IMAGE_MIME_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


class ReadTool(FunctionTool):
    def __init__(self, config: ReadToolConfig) -> None:
        """Initialize the ReadTool with constraint configuration.

        Args:
            config: Controls file access constraints. Key fields:
                - working_dir: Base directory for resolving relative patterns.
                - rules: Ordered list of rules (first match wins).
                - default_policy: Policy when no rules match (default: ASK).

        Pattern syntax:
            - `//path` - Absolute path from filesystem root (e.g., `//etc/hosts`)
            - `C:/path` or `C:\\path` - Absolute Windows drive path (e.g., `C:/Users/`)
            - `~/path` - Path from home directory (e.g., `~/Documents/*.pdf`)
            - `/path` or `./path` - Path from working_dir root (e.g., `/src/**`)
            - `path` - Path under working_dir; bare filenames match at any depth.

        Example:
            Rules are evaluated top-to-bottom; place specific rules before general ones.

            config = ReadToolConfig(
                working_dir=Path("/home/user/project"),
                rules=[
                    # Relative patterns resolve under working_dir
                    ReadConstraintRule(pattern="secrets/**", policy=ConstraintPolicy.DENY),
                    ReadConstraintRule(pattern="src/**", policy=ConstraintPolicy.ALLOW),
                    # Home and absolute filesystem patterns use explicit anchors
                    ReadConstraintRule(pattern="~/Documents/*.pdf", policy=ConstraintPolicy.ALLOW),
                    ReadConstraintRule(pattern="//etc/hosts", policy=ConstraintPolicy.ALLOW),
                    ReadConstraintRule(pattern="C:/**/.env", policy=ConstraintPolicy.DENY),
                    ReadConstraintRule(pattern="//etc/**", policy=ConstraintPolicy.DENY),
                ],
            )
            tool = ReadTool(config)
        """
        self.config = config

    def get_tool_defs(self) -> dict[str, FunctionToolParam]:
        return {TOOL_NAME: READ_TOOL_DEFINITION}

    def execute(self, **arguments: object) -> str | ResponseFunctionCallOutputItemListParam:
        """Read a file and return its contents.

        Assumes caller has already verified permission via check_constraint().

        Args:
            **arguments: Tool arguments containing:
                - file_path: Absolute path to the file to read.
                - offset: Number of lines to skip (for text files). Defaults to 0.
                - limit: Maximum number of lines to read (for text files). Defaults to 2000.

        Returns:
            For text files: string in cat -n format with line numbers.
            For images: list containing ResponseInputImageContentParam.
            For PDFs: markdown string extracted from the PDF.
            On error: string describing the error.
        """
        file_path = str(arguments.get("file_path", ""))
        offset_arg = arguments.get("offset")
        limit_arg = arguments.get("limit")

        offset = int(str(offset_arg)) if offset_arg is not None else 0
        limit = int(str(limit_arg)) if limit_arg is not None else 2000

        path = Path(file_path)

        if not path.exists():
            return f"Error: File not found: {file_path}"
        if path.is_dir():
            return f"Error: Path is a directory, not a file: {file_path}"

        suffix = path.suffix.lower()

        if suffix == ".pdf":
            try:
                return _read_pdf(path)
            except Exception as e:
                return f"Error reading PDF: {e}"

        if suffix in IMAGE_EXTENSIONS:
            try:
                return _read_image(path)
            except Exception as e:
                return f"Error reading image: {e}"

        if suffix == ".ipynb":
            return "Error: Jupyter notebook files (.ipynb) are not supported"

        try:
            return _read_text(path, offset, limit)
        except Exception as e:
            return f"Error reading file: {e}"

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


def _read_pdf(file_path: Path) -> str:
    """Extract text content from a PDF file as markdown."""
    return pymupdf4llm.to_markdown(file_path)


def _read_image(file_path: Path) -> ResponseFunctionCallOutputItemListParam:
    """Read an image file and return it as base64-encoded content."""
    suffix = file_path.suffix.lower()
    mime_type = IMAGE_MIME_TYPES[suffix]
    encoded = base64.b64encode(file_path.read_bytes()).decode("utf-8")
    content: ResponseInputImageContentParam = {"type": "input_image", "image_url": f"data:{mime_type};base64,{encoded}"}
    return [content]


def _read_text(file_path: Path, offset: int, limit: int) -> str:
    """Read a text file with offset/limit and format as cat -n output.

    Args:
        file_path: Path to the text file.
        offset: Number of lines to skip from the beginning.
        limit: Maximum number of lines to read after the offset.

    Returns:
        Content formatted as cat -n with line numbers starting at offset + 1.
    """
    with file_path.open(encoding="utf-8", errors="replace") as f:
        lines = f.readlines()

    selected = lines[offset : offset + limit]

    result_lines = []
    for i, line in enumerate(selected, start=offset + 1):
        line = line.rstrip("\n\r")
        if len(line) > 2000:
            line = line[:2000]
        result_lines.append(f"{i:6}\t{line}")

    return "\n".join(result_lines)
