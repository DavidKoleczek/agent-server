<h1 align="center">
    agent-server
</h1>
<p align="center">
    <p align="center">Fast, responsive agent server.</p>
</p>
<p align="center">
    <a href="https://github.com/astral-sh/uv"><img src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json" alt="uv"></a>
    <a href="https://github.com/astral-sh/ty"><img src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ty/main/assets/badge/v0.json" alt="ty"></a>
    <a href="https://opensource.org/licenses/MIT"><img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License: MIT"></a>
</p>

> [!NOTE]
> This library is in early development and subject to change.

The server that powers [agent-tui](https://github.com/DavidKoleczek/agent-tui). It is a general server that can be used to power agent driven experiences. 

The goals of this server are to make interacting with agents feel more responsive.


## Usage

Start the server:

```bash
uv run agent-server
```

Connect to the WebSocket and send a message:

```python
import asyncio
import json
import websockets

async def main():
    async with websockets.connect("ws://127.0.0.1:8000/agent") as ws:
        await ws.send(json.dumps({"type": "user_message", "content": "Hello!"}))
        async for message in ws:
            print(json.loads(message))

asyncio.run(main())
```

See `scripts/ws_client.py` for a full interactive client.


## Documentation

[Documentation](./docs/README.md)
