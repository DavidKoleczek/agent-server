from typing import Literal, get_args, get_origin

from fastapi import APIRouter
from pydantic import BaseModel

from agent_server.schemas.activity import SessionConfigChangeEvent
from agent_server.schemas.session import SessionConfig

router = APIRouter()


class ConfigOption(BaseModel):
    key: str
    label: str
    values: list[str]
    default: str


class SessionCapabilities(BaseModel):
    options: list[ConfigOption]


@router.get("/capabilities")
async def capabilities() -> SessionCapabilities:
    """
    Advertise the session config options a client may change via SessionConfigChangeEvent.

    Returns each changeable config key with a display label, its full set of valid values, and its
    default, so a client can present the choices and know what a SessionConfigChangeEvent may carry.
    """
    config_keys = get_args(SessionConfigChangeEvent.model_fields["config_key"].annotation)
    options: list[ConfigOption] = []
    for key in config_keys:
        field = SessionConfig.model_fields[key]
        label = field.title or key.replace("_", " ").title()
        options.append(
            ConfigOption(
                key=key,
                label=label,
                values=_literal_values(field.annotation),
                default=str(field.default),
            )
        )
    return SessionCapabilities(options=options)


def _literal_values(annotation: object) -> list[str]:
    """Flatten a Literal[...] or a union of Literal[...] into its string values."""
    if get_origin(annotation) is Literal:
        return [str(value) for value in get_args(annotation)]
    values: list[str] = []
    for arg in get_args(annotation):
        values.extend(_literal_values(arg))
    return values
