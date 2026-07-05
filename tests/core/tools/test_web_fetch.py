import pytest

from agent_server.core.tools import web_fetch
from agent_server.core.tools.web_fetch import WebFetchTool
from agent_server.core.web.process_url import URLResult


async def test_execute_returns_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_process_url(url: str) -> URLResult:
        return URLResult(url=url, html="<h1>Example</h1>", markdown="# Example")

    monkeypatch.setattr(web_fetch, "process_url", fake_process_url)

    tool = WebFetchTool()
    result = await tool.execute(url="https://example.com/")

    assert result == "# Example"
