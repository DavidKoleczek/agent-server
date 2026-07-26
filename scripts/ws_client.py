"""Run the interactive agent WebSocket development client."""

import argparse
import asyncio
from pathlib import Path
import sys

from ws_client_support.client import AGENT_CONFIG, ClientError, ClientOptions, run_client


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Connect to agent-server with an interactive WebSocket client.")
    parser.add_argument(
        "--working-dir",
        type=Path,
        help="Existing directory the agent will operate in. Defaults to AGENT_CONFIG.",
    )
    parser.add_argument(
        "--session-database",
        type=Path,
        help="SQLite session database to create or resume. Defaults to AGENT_CONFIG.",
    )
    parser.add_argument(
        "--default-model",
        help="Initial model for sessions without stored configuration. Defaults to AGENT_CONFIG.",
    )
    parser.add_argument(
        "--server-url",
        default="ws://127.0.0.1:8000/agent",
        help="Agent WebSocket URL. Defaults to ws://127.0.0.1:8000/agent.",
    )
    return parser


def _parse_options() -> ClientOptions:
    parser = _build_parser()
    args = parser.parse_args()

    working_dir = (args.working_dir or AGENT_CONFIG.working_dir).expanduser().resolve()
    if not working_dir.is_dir():
        parser.error(f"--working-dir must be an existing directory: {working_dir}")

    session_database = args.session_database or AGENT_CONFIG.session_database
    if session_database is not None:
        session_database = session_database.expanduser().resolve()

    return ClientOptions(
        server_url=args.server_url,
        agent_config=AGENT_CONFIG.model_copy(
            update={
                "working_dir": working_dir,
                "session_database": session_database,
                "default_model": args.default_model or AGENT_CONFIG.default_model,
            }
        ),
    )


def main() -> int:
    try:
        asyncio.run(run_client(_parse_options()))
    except KeyboardInterrupt:
        return 130
    except ClientError as exc:
        print(f"[client error] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
