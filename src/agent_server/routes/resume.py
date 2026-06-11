from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from agent_server.schemas.session import SessionActivityRecord
from agent_server.storage.session_store import SessionStore

router = APIRouter()


@router.get("/resume")
async def resume(
    working_dir: Annotated[Path, Query(description="Directory the session was working in.")],
    session_database: Annotated[Path, Query(description="Path to the existing SQLite session database to resume.")],
) -> list[SessionActivityRecord]:
    """
    Resume a prior session by loading its activity history.

    Opens the provided session database and returns its full list of SessionActivityRecord. The
    database must already exist; a missing file results in a 404 rather than creating an empty one.
    """
    if not session_database.is_file():
        raise HTTPException(status_code=404, detail=f"Session database does not exist: {session_database}")

    with SessionStore(session_database) as session_store:
        return session_store.load_session_activities()
