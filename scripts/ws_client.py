"""
Commands:
/cancel   Send a CancelActivity.
/quit     Send a QuitActivity (server will close the connection).
/exit     Close the client locally without sending anything.
<text>    Send a UserActivity with the given content.
"""

import asyncio
import contextlib
import json
import sys
from typing import Any

import websockets
from websockets.asyncio.client import ClientConnection

URL = "ws://127.0.0.1:8000/activity"


async def reader(ws: ClientConnection) -> None:
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
        sys.stdout.write("> ")
        sys.stdout.flush()
        line = await loop.run_in_executor(None, sys.stdin.readline)
        if not line:
            await ws.close()
            return
        text = line.strip()
        if not text:
            continue

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
        for task in pending:
            task.cancel()
        for task in done:
            exc = task.exception()
            if exc is not None and not isinstance(exc, websockets.ConnectionClosed):
                raise exc


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
