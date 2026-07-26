"""WebSocket connection and terminal input orchestration.
Fully AI-generated"""

import asyncio
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re
import sys
import threading
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

import httpx
from pydantic import TypeAdapter
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

from agent_server.schemas.agent_config import AgentConfig
from agent_server.schemas.session import SessionActivityRecord
from ws_client_support.commands import CommandError, CommandProcessor
from ws_client_support.display import EventDisplay, Terminal

AGENT_CONFIG = AgentConfig(
    working_dir=Path.cwd(),
    session_database=None,
    default_model="gpt-5.6-luna",
)

_SESSION_ACTIVITY_RECORDS_ADAPTER = TypeAdapter(list[SessionActivityRecord])
_CONNECT_TIMEOUT_SECONDS = 30.0
_CONNECT_RETRY_SECONDS = 0.5


class ClientError(RuntimeError):
    pass


@dataclass(frozen=True)
class ClientOptions:
    server_url: str
    agent_config: AgentConfig


class _StdinPump:
    """Move blocking stdin reads onto a daemon thread so a server disconnect can still end the client."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[str | OSError | ValueError | None] = asyncio.Queue()

    def start(self) -> None:
        loop = asyncio.get_running_loop()

        def read_stdin() -> None:
            try:
                while line := sys.stdin.readline():
                    _submit_stdin_item(loop, self._queue, line)
            except (OSError, ValueError) as exc:
                _submit_stdin_item(loop, self._queue, exc)
                return
            _submit_stdin_item(loop, self._queue, None)

        threading.Thread(target=read_stdin, name="ws-client-stdin", daemon=True).start()

    async def read(self) -> str | None:
        item = await self._queue.get()
        if isinstance(item, OSError | ValueError):
            raise item
        return item


def _submit_stdin_item(
    loop: asyncio.AbstractEventLoop,
    queue: asyncio.Queue[str | OSError | ValueError | None],
    item: str | OSError | ValueError | None,
) -> None:
    try:
        loop.call_soon_threadsafe(queue.put_nowait, item)
    except RuntimeError:
        return


def _new_session_database(working_dir: Path) -> Path:
    sanitized_name = re.sub(r'[<>:"/\\|?*\s]', "_", working_dir.name)
    timestamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    return working_dir / ".agents" / "sessions" / f"{sanitized_name}_{timestamp}_{uuid4().hex[:8]}.sqlite"


def _build_websocket_url(server_url: str, agent_config: AgentConfig) -> str:
    parsed = urlsplit(server_url)
    if parsed.scheme not in {"ws", "wss"} or not parsed.netloc:
        raise ValueError(f"Invalid WebSocket URL: {server_url}")
    if parsed.fragment:
        raise ValueError("The WebSocket URL must not contain a fragment.")
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.update(agent_config.model_dump(mode="json", exclude_none=True))
    path = parsed.path or "/agent"
    return urlunsplit((parsed.scheme, parsed.netloc, path, urlencode(query), ""))


def _build_resume_url(server_url: str, working_dir: Path, session_database: Path) -> str:
    parsed = urlsplit(server_url)
    scheme = {"ws": "http", "wss": "https"}.get(parsed.scheme)
    if scheme is None or not parsed.netloc:
        raise ValueError(f"Invalid WebSocket URL: {server_url}")

    route_prefix, _, _ = parsed.path.rstrip("/").rpartition("/")
    path = f"{route_prefix}/resume"
    query = urlencode(
        {
            "working_dir": str(working_dir),
            "session_database": str(session_database),
        }
    )
    return urlunsplit((scheme, parsed.netloc, path, query, ""))


async def _load_existing_session(
    server_url: str,
    working_dir: Path,
    session_database: Path,
) -> list[SessionActivityRecord]:
    resume_url = _build_resume_url(server_url, working_dir, session_database)
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(resume_url)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise ClientError(f"Could not load session {session_database}: {exc}") from exc
    return _SESSION_ACTIVITY_RECORDS_ADAPTER.validate_json(response.content)


async def _connect(
    url: str,
    terminal: Terminal,
) -> ClientConnection:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _CONNECT_TIMEOUT_SECONDS
    retry_message_shown = False

    while True:
        try:
            return await connect(url)
        except OSError as exc:
            if loop.time() >= deadline:
                raise ClientError(
                    "Could not connect to agent-server at "
                    f"{urlsplit(url).scheme}://{urlsplit(url).netloc}. "
                    "Start the server with 'uv run agent-server --reload'."
                ) from exc
            if not retry_message_shown:
                await terminal.write_line(
                    f"[client] agent-server is not ready; retrying for {_CONNECT_TIMEOUT_SECONDS:g} seconds"
                )
                retry_message_shown = True
            await asyncio.sleep(_CONNECT_RETRY_SECONDS)


async def _read_events(ws: ClientConnection, display: EventDisplay) -> str | None:
    try:
        async for message in ws:
            await display.handle_message(message)
    except ConnectionClosed as exc:
        return str(exc)
    return None


async def _write_events(
    ws: ClientConnection,
    terminal: Terminal,
    commands: CommandProcessor,
    stdin: _StdinPump,
) -> None:
    while True:
        await terminal.show_prompt()
        line = await stdin.read()
        await terminal.consume_prompt()
        if line is None:
            await terminal.write_line("[client] stdin closed; the WebSocket connection remains open")
            return

        text = line.strip()
        if not text:
            continue

        try:
            result = commands.parse(text)
        except CommandError as exc:
            await terminal.write_line(f"[client error] {exc}")
            continue

        if result.message is not None:
            await terminal.write_lines(result.message.splitlines())
        if result.event is not None:
            try:
                await ws.send(result.event.model_dump_json())
            except ConnectionClosed:
                return
        if result.close_local:
            await terminal.write_line("[client] closing local connection")
            await ws.close()
            return
        if result.stop_input:
            return


async def run_client(options: ClientOptions) -> None:
    working_dir = options.agent_config.working_dir
    session_database = options.agent_config.session_database or _new_session_database(working_dir)
    agent_config = options.agent_config.model_copy(update={"session_database": session_database})
    resume_session = session_database.is_file()
    session_database.parent.mkdir(parents=True, exist_ok=True)
    url = _build_websocket_url(options.server_url, agent_config)

    terminal = Terminal(interactive=sys.stdin.isatty())
    display = EventDisplay(terminal)
    commands = CommandProcessor(display)
    stdin = _StdinPump()

    await terminal.write_lines(
        [
            f"[client] working directory: {working_dir}",
            f"[client] session database: {session_database}",
            f"[client] default model: {agent_config.default_model or 'server default'}",
            f"[client] connecting to: {url}",
        ]
    )
    ws = await _connect(url, terminal)
    try:
        if resume_session:
            records = await _load_existing_session(
                options.server_url,
                working_dir,
                session_database,
            )
            await terminal.write_line(f"[client] loaded {len(records)} persisted activities")
            await display.load_activities([record.activity for record in records])

        await terminal.write_line("[client] connected; use /help to list commands")
        stdin.start()
        reader_task = asyncio.create_task(_read_events(ws, display))
        writer_task = asyncio.create_task(_write_events(ws, terminal, commands, stdin))
        close_detail: str | None = None

        try:
            while True:
                done, _ = await asyncio.wait({reader_task, writer_task}, return_when=asyncio.FIRST_COMPLETED)
                if reader_task in done:
                    close_detail = await reader_task
                    break
                if writer_task in done:
                    exception = writer_task.exception()
                    if exception is not None:
                        raise exception
                    close_detail = await reader_task
                    break
        finally:
            for task in (reader_task, writer_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(reader_task, writer_task, return_exceptions=True)
    finally:
        await ws.close()

    message = "[client] connection closed"
    if close_detail is not None:
        message = f"{message}: {close_detail}"
    await terminal.write_line(message)
