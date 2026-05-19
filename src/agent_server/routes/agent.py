import asyncio

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
)

router = APIRouter()

_CLIENT_ACTIVITY_ADAPTER = TypeAdapter(ClientActivity)


@router.websocket("/agent")
async def agent_endpoint(websocket: WebSocket) -> None:
    """
    Websocket that handles realtime interaction with the agent.
    It uses the AgentManager to create agent processes and send information back and forth between it and the client.
    """
    agent_activities: asyncio.Queue[AssistantActivity] = asyncio.Queue()
    agent_manager = AgentManager(agent_activities=agent_activities)
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
            await websocket.send_json(message.model_dump(mode="json"))
    except (asyncio.CancelledError, WebSocketDisconnect):
        return
