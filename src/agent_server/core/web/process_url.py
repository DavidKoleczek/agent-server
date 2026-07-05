import asyncio
import io
import mimetypes
from pathlib import Path
import re
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel

_HAS_WEB_DEPS = True

try:
    from crawl4ai import AsyncWebCrawler, CacheMode
    from crawl4ai.async_configs import BrowserConfig, CrawlerRunConfig
    from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator
    from crawl4ai.processors.pdf import PDFContentScrapingStrategy, PDFCrawlerStrategy
    import feedparser
    from markitdown import MarkItDown, StreamInfo
except ImportError:
    _HAS_WEB_DEPS = False

    if TYPE_CHECKING:
        from crawl4ai import AsyncWebCrawler, CacheMode
        from crawl4ai.async_configs import BrowserConfig, CrawlerRunConfig
        from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator
        from crawl4ai.processors.pdf import PDFContentScrapingStrategy, PDFCrawlerStrategy
        import feedparser
        from markitdown import MarkItDown, StreamInfo


class Link(BaseModel):
    url: str
    text: str


class URLResult(BaseModel):
    url: str
    html: str
    markdown: str
    links: list[Link] = []


async def _markitdown_bytes_to_str(file_bytes: bytes, filename_extension: str) -> str:
    """
    Convert a file using MarkItDown defaults.
    """
    with io.BytesIO(file_bytes) as temp:
        result = await asyncio.to_thread(
            MarkItDown(enable_plugins=False).convert,
            source=temp,
            stream_info=StreamInfo(extension=filename_extension),
        )
        text = result.text_content
    return text


def _detect_pdf_extension(url: str) -> bool:
    """
    Detect if the URL is a PDF based on its extension.
    """
    parsed_url = urlparse(url)
    filename = Path(parsed_url.path).name
    return mimetypes.guess_type(filename)[0] == "application/pdf"


def _detect_google_sheets(url: str) -> bool:
    """
    Detect if the URL is a Google Sheets document.
    """
    is_google_sheets = url.startswith("https://docs.google.com/spreadsheets/")
    return is_google_sheets


def _detect_rss_feed(url: str) -> bool:
    """
    Detect if the URL is likely an RSS feed by checking URL patterns.
    """
    url_lower = url.lower()

    # Check file extensions
    if url_lower.endswith((".rss", ".xml", ".atom", ".feed")):
        return True

    # Check common RSS URL patterns
    rss_patterns = [
        "/rss",
        "/feed",
        "/atom",
        "/rss.xml",
        "/feed.xml",
        "/atom.xml",
        "/index.rss",
        "/index.xml",
        "format=rss",
        "format=atom",
        "?feed=",
        "&feed=",
        "/feeds/",
        ".rss2",
        "/rdf",
    ]

    return any(pattern in url_lower for pattern in rss_patterns)


async def _handle_pdf_content(url: str) -> URLResult:
    browser_config = BrowserConfig(
        browser_type="undetected",
        headless=True,
        verbose=False,
        user_agent_mode="random",
        java_script_enabled=False,
        extra_args=["--disable-blink-features=AutomationControlled", "--disable-web-security"],
    )
    pdf_crawler_strategy = PDFCrawlerStrategy()
    pdf_scraping_strategy = PDFContentScrapingStrategy()
    run_config = CrawlerRunConfig(
        scraping_strategy=pdf_scraping_strategy,
        user_agent_mode="random",
        cache_mode=CacheMode.DISABLED,
    )
    markdown = ""
    async with AsyncWebCrawler(crawler_strategy=pdf_crawler_strategy, config=browser_config) as crawler:
        result = await crawler.arun(url=url, config=run_config)
        if result.success:
            if result.markdown and hasattr(result.markdown, "raw_markdown"):
                markdown = result.markdown.raw_markdown
        else:
            markdown = result.error_message

    url_result = URLResult(url=url, html="", markdown=markdown)
    return url_result


async def _handle_rss_feed(url: str) -> URLResult:
    """
    Handle RSS/Atom feeds by parsing them and converting to markdown.
    """

    async with httpx.AsyncClient(follow_redirects=True) as client:
        response = await client.get(url, timeout=10.0)
        response.raise_for_status()
        content = response.text
    feed = await asyncio.to_thread(feedparser.parse, content)

    markdown_lines = []
    links = []
    if hasattr(feed, "feed"):
        feed_obj = feed.feed
        feed_title = getattr(feed_obj, "title", None)
        if feed_title:
            markdown_lines.append(f"# {feed_title}")

        feed_description = getattr(feed_obj, "description", None)
        if feed_description:
            markdown_lines.append(f"\n{feed_description}\n")

        feed_link = getattr(feed_obj, "link", None)
        if feed_link:
            markdown_lines.append(f"Feed URL: {feed_link}\n")

    markdown_lines.append("\n## Feed Entries\n")
    if hasattr(feed, "entries"):
        for entry in feed.entries:
            if hasattr(entry, "title"):
                markdown_lines.append(f"\n### {entry.title}\n")

            if hasattr(entry, "published"):
                markdown_lines.append(f"**Published:** {entry.published}\n")

            entry_link = getattr(entry, "link", None)
            entry_title = getattr(entry, "title", "")
            if entry_link:
                markdown_lines.append(f"**Link:** {entry_link}\n")
                links.append(Link(url=entry_link, text=entry_title))

            if hasattr(entry, "summary"):
                markdown_lines.append(f"\n{entry.summary}\n")
            elif hasattr(entry, "description"):
                markdown_lines.append(f"\n{entry.description}\n")

            if hasattr(entry, "content"):
                for content_item in entry.content:
                    if hasattr(content_item, "value"):
                        markdown_lines.append(f"\n{content_item.value}\n")

            markdown_lines.append("\n---\n")

    markdown = "\n".join(markdown_lines)
    return URLResult(
        url=url,
        html=content,
        markdown=markdown,
        links=links,
    )


async def _handle_google_sheets_content(url: str) -> URLResult:
    """
    Handle Google Sheets by using the export URL to get the raw content.
    """
    edit_pattern = r"https://docs\.google\.com/spreadsheets/d/([a-zA-Z0-9-_]+)/edit"
    export_pattern = r"https://docs\.google\.com/spreadsheets/d/([a-zA-Z0-9-_]+)/export\?format=csv"

    # Check if it's already an export URL
    export_match = re.search(export_pattern, url)
    if export_match:
        export_url = url
    else:
        # Check if it's an edit URL and extract document ID
        edit_match = re.search(edit_pattern, url)
        if edit_match:
            doc_id = edit_match.group(1)
            export_url = f"https://docs.google.com/spreadsheets/d/{doc_id}/export?format=csv&gid=0"
        else:
            return await _handle_web_content(url)

    async with httpx.AsyncClient(follow_redirects=True) as client:
        response = await client.get(export_url)
        response.raise_for_status()
        csv_bytes = response.content

    # Convert CSV to markdown using MarkItDown
    markdown_content = await _markitdown_bytes_to_str(csv_bytes, ".csv")

    url_result = URLResult(
        url=url,
        html="",
        markdown=markdown_content,
        links=[],
    )
    return url_result


async def _handle_web_content(url: str, verbose: bool = False) -> URLResult:
    browser_config = BrowserConfig(
        browser_type="undetected",
        headless=True,
        verbose=verbose,
        user_agent_mode="random",
        java_script_enabled=True,
        extra_args=["--disable-blink-features=AutomationControlled", "--disable-web-security"],
    )
    run_config = CrawlerRunConfig(
        scan_full_page=True,
        user_agent_mode="random",
        cache_mode=CacheMode.DISABLED,
        markdown_generator=DefaultMarkdownGenerator(),
        verbose=verbose,
    )

    async with AsyncWebCrawler(config=browser_config) as crawler:
        result = await crawler.arun(
            url=url,
            config=run_config,
        )

        if result.response_headers.get("content-type") == "application/pdf":
            return await _handle_pdf_content(url)

    links: list[Link] = []
    seen_urls: set[str] = set()
    combined_link_data = result.links.get("internal", []) + result.links.get("external", [])
    for link_data in combined_link_data:
        href = link_data.get("href", "")
        if href and href not in seen_urls:
            seen_urls.add(href)
            link = Link(
                url=href,
                text=link_data.get("title", "") or link_data.get("text", ""),
            )
            links.append(link)

    url_result = URLResult(
        url=url,
        html=result.html or "",
        markdown=result.markdown or "",
        links=links,
    )
    return url_result


async def process_url(url: str, verbose: bool = False) -> URLResult:
    if not _HAS_WEB_DEPS:
        raise ImportError("Web fetching requires additional dependencies.")

    if _detect_pdf_extension(url):
        url_result = await _handle_pdf_content(url)
    elif _detect_google_sheets(url):
        url_result = await _handle_google_sheets_content(url)
    elif _detect_rss_feed(url):
        url_result = await _handle_rss_feed(url)
    else:
        url_result = await _handle_web_content(url, verbose=verbose)
    return url_result
