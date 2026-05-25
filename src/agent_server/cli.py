import argparse

import uvicorn


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-server",
        description="Run the agent-server HTTP and WebSocket service.",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Interface to bind. Defaults to 127.0.0.1.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port to bind. Defaults to 8000.",
    )
    parser.add_argument(
        "--reload",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Enable uvicorn auto-reload. Off by default; pass --reload for development.",
    )
    return parser


def run() -> None:
    args = _build_parser().parse_args()
    # The app is passed as an import string so uvicorn can reimport it on reload.
    uvicorn.run("agent_server.main:app", host=args.host, port=args.port, reload=args.reload)
