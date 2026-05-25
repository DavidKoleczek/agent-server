import asyncio
from pathlib import Path

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import TypeAdapter, ValidationError

from agent_server.agent.agent_manager import AgentManager
from agent_server.schemas.activity import (
    AssistantActivity,
    CancelActivity,
    ClientActivity,
    ErrorActivity,
    QuitActivity,
    UserActivity,
    serialize_activity,
)

router = APIRouter()

_CLIENT_ACTIVITY_ADAPTER = TypeAdapter(ClientActivity)

# Captured at import time so it reflects the directory the server was started in.
_SERVER_DIR = Path.cwd()


@router.websocket("/agent")
async def agent_endpoint(websocket: WebSocket) -> None:
    """
    Websocket that handles realtime interaction with the agent.
    It uses the AgentManager to create agent processes and send information back and forth between it and the client.
    """
    working_dir_param = websocket.query_params.get("working_dir")
    chat_file_param = websocket.query_params.get("chat_file")

    working_dir = Path(working_dir_param) if working_dir_param else _SERVER_DIR
    chat_file = Path(chat_file_param) if chat_file_param else None

    agent_activities: asyncio.Queue[AssistantActivity] = asyncio.Queue()
    agent_manager = AgentManager(
        agent_activities=agent_activities,
        working_dir=working_dir,
        chat_file=chat_file,
    )
    # Start the runner
    runner = asyncio.create_task(agent_manager.runner())
    await websocket.accept()
    forwarder = asyncio.create_task(_forward_outbound(websocket, agent_activities))
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                activity: ClientActivity = _CLIENT_ACTIVITY_ADAPTER.validate_json(raw)
            except ValidationError as exc:
                await agent_activities.put(
                    ErrorActivity(type="error", error_type="invalid_client_activity_format", detail=str(exc))
                )
                continue

            match activity:
                case UserActivity():
                    await agent_manager.submit_user_activity(activity)
                case CancelActivity():
                    await agent_manager.cancel()
                case QuitActivity():
                    await websocket.close()
                    return
    except WebSocketDisconnect:
        return
    finally:
        forwarder.cancel()
        runner.cancel()


async def _forward_outbound(
    websocket: WebSocket,
    agent_activities: asyncio.Queue[AssistantActivity],
) -> None:
    """Sole writer to the websocket. Drains the agent activities queue and sends each message as JSON."""
    try:
        while True:
            message = await agent_activities.get()
            await websocket.send_json(serialize_activity(message))
    except (asyncio.CancelledError, WebSocketDisconnect):
        return
