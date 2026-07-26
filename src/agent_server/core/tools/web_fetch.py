from openai.types.responses import ResponseFunctionCallOutputItemListParam
from openai.types.responses.function_tool_param import FunctionToolParam

from agent_server.core.tools._protocol import FunctionTool
from agent_server.core.tools._utils import ConstraintPolicy
from agent_server.core.web.process_url import process_url

TOOL_NAME = "web_fetch"

TOOL_DESCRIPTION = """- Fetches content from a specified URL
- Takes a URL and a prompt as input
- Fetches the URL content, converts HTML to markdown
- Use this tool when you need to retrieve and analyze web content

Usage notes:
  - The URL must be a fully-formed valid URL
  - HTTP URLs will be automatically upgraded to HTTPS
  - This tool is read-only and does not modify any files
  - Results may be summarized or truncated if the content is very large
  - When a URL redirects to a different host, the tool will inform you and provide the redirect URL in a special format. You should then make a new WebFetch request with the redirect URL to fetch the content."""

WEB_FETCH_DEFINITION: FunctionToolParam = {
    "type": "function",
    "name": TOOL_NAME,
    "description": TOOL_DESCRIPTION,
    "parameters": {
        "type": "object",
        "properties": {
            "url": {"description": "The URL to fetch content from", "type": "string"},
        },
        "required": ["url"],
        "additionalProperties": False,
    },
    "strict": True,
}


class WebFetchTool(FunctionTool):
    def __init__(self) -> None:
        pass

    def get_tool_defs(self) -> dict[str, FunctionToolParam]:
        return {TOOL_NAME: WEB_FETCH_DEFINITION}

    def check_constraint(self, **arguments: object) -> ConstraintPolicy:
        return ConstraintPolicy.ALLOW

    async def execute(self, **arguments: object) -> str | ResponseFunctionCallOutputItemListParam:
        url = str(arguments.get("url"))
        result = await process_url(url)
        markdown = result.markdown
        return markdown
