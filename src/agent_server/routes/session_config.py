from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from agent_server.schemas.session import SessionConfig
from agent_server.storage.session_store import SessionStore

router = APIRouter()


@router.get("/session-config")
async def session_config(
    session_database: Annotated[Path, Query(description="Path to the existing SQLite session database.")],
) -> SessionConfig:
    """
    Return the current configuration for a session.

    Opens the provided session database and returns its stored SessionConfig so a client can populate its initial display.
    The database must already exist; a missing file results in a 404 so this should be called after the agent websocket is connected.
    """
    if not session_database.is_file():
        raise HTTPException(status_code=404, detail=f"Session database does not exist: {session_database}")

    with SessionStore(session_database) as session_store:
        return session_store.load_session_config()
