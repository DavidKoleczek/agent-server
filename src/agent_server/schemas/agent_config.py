from pathlib import Path

from pydantic import BaseModel, Field


class AgentConfig(BaseModel):
    working_dir: Path = Field(description="Directory the agent is working in.")
    session_database: Path | None = Field(
        default=None,
        description="Path to the SQLite session database. If None, a new database is created in .agents/sessions.",
    )
    default_model: str | None = Field(
        default=None,
        description="Default model used when the session has no stored configuration.",
    )
