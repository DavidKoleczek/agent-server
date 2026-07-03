"""
Commands:
/cancel   Send a CancelActivity.
/quit     Send a QuitActivity (server will close the connection).
/exit     Close the client locally without sending anything.
<text>    Send a UserActivity with the given content.
"""

import asyncio
import contextlib
from datetime import datetime
import json
from pathlib import Path
import re
import sys
from typing import Any
from urllib.parse import quote
import uuid

import websockets
from websockets.asyncio.client import ClientConnection


def _session_database_path() -> Path:
    """Mirror the server's former auto-generated session db path so this client owns an equivalent path."""
    working_dir = Path.cwd()
    sessions_dir = working_dir / ".agents" / "sessions"
    sanitized_name = re.sub(r'[<>:"/\\|?*\s]', "_", working_dir.name)
    date_str = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    short_uuid = str(uuid.uuid4())[:8]
    return sessions_dir / f"{sanitized_name}_{date_str}_{short_uuid}.sqlite"


URL = f"ws://127.0.0.1:8000/agent?session_database={quote(str(_session_database_path()))}"


async def reader(ws: ClientConnection) -> None:
    # Reads in messages as json from the server and prints them to stdout.
    async for message in ws:
        text = message if isinstance(message, str) else message.decode("utf-8", errors="replace")
        try:
            parsed = json.loads(text)
            rendered = json.dumps(parsed, indent=2)
        except json.JSONDecodeError:
            rendered = text
        sys.stdout.write(f"\n<-- {rendered}\n> ")
        sys.stdout.flush()


async def writer(ws: ClientConnection) -> None:
    loop = asyncio.get_running_loop()
    while True:
        # Read user input from stdin
        sys.stdout.write("> ")
        sys.stdout.flush()
        line = await loop.run_in_executor(None, sys.stdin.readline)
        if not line:
            await ws.close()
            return
        text = line.strip()
        if not text:
            continue

        # Create payload for the server based on the message sent.
        payload: dict[str, Any]
        if text == "/exit":
            await ws.close()
            return
        if text == "/cancel":
            payload = {"type": "cancel"}
        elif text == "/quit":
            payload = {"type": "quit"}
        else:
            payload = {"type": "user_message", "content": text}

        await ws.send(json.dumps(payload))


async def main() -> None:
    async with websockets.connect(URL) as ws:
        sys.stdout.write(f"connected to {URL}\n")
        sys.stdout.flush()
        reader_task = asyncio.create_task(reader(ws))
        writer_task = asyncio.create_task(writer(ws))
        done, pending = await asyncio.wait({reader_task, writer_task}, return_when=asyncio.FIRST_COMPLETED)

        # Shutdown/Cleanup
        for task in pending:
            task.cancel()
        for task in done:
            exc = task.exception()
            if exc is not None and not isinstance(exc, websockets.ConnectionClosed):
                raise exc


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
