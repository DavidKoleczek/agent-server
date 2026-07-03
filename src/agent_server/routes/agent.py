import asyncio
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import TypeAdapter, ValidationError

from agent_server.agent.agent_manager import AgentManager
from agent_server.schemas.activity import (
    ActivityCreatedEvent,
    CancelEvent,
    ClientEvent,
    ErrorActivity,
    QuitEvent,
    StreamingEvent,
    UserMessageEvent,
)

router = APIRouter()

_CLIENT_ACTIVITY_ADAPTER = TypeAdapter(ClientEvent)

# Captured at import time so it reflects the directory the server was started in.
_SERVER_DIR = Path.cwd()


@router.websocket("/agent")
async def agent_endpoint(websocket: WebSocket) -> None:
    """
    Websocket that handles realtime interaction with the agent.
    It uses the AgentManager to create agent processes and send information back and forth between it and the client.
    """
    working_dir_param = websocket.query_params.get("working_dir")
    session_database_param = websocket.query_params.get("session_database")

    await websocket.accept()
    # The client owns the session database path; reject connections that do not provide one.
    if not session_database_param:
        await websocket.close(code=1008, reason="session_database query parameter is required")
        return

    working_dir = Path(working_dir_param) if working_dir_param else _SERVER_DIR
    session_database = Path(session_database_param)

    streaming_events: asyncio.Queue[StreamingEvent] = asyncio.Queue()
    agent_manager = AgentManager(
        streaming_events=streaming_events,
        working_dir=working_dir,
        session_database=session_database,
    )
    # Start the runner
    agent_manager_task = asyncio.create_task(agent_manager.start_manager())
    forwarder = asyncio.create_task(_forward_outbound(websocket, streaming_events))
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                client_event: ClientEvent = _CLIENT_ACTIVITY_ADAPTER.validate_json(raw)
            except ValidationError as exc:
                await streaming_events.put(
                    ActivityCreatedEvent(
                        activity=ErrorActivity(
                            id=str(uuid4()),
                            state="error",
                            error_type="invalid_client_activity_format",
                            detail=str(exc),
                        )
                    )
                )
                continue

            match client_event:
                case UserMessageEvent():
                    await agent_manager.submit_user_event(client_event)
                case CancelEvent():
                    # The cancel event kills the agent worker and restarts it (thus stopping any ongoing work).
                    await agent_manager.cancel()
                case QuitEvent():
                    # Quit is for a clean shutdown of the websocket, including properly stopping any processes.
                    await _stop_agent_manager(agent_manager, agent_manager_task)
                    await websocket.close()
                    return
    except WebSocketDisconnect:
        return
    finally:
        await _stop_agent_manager(agent_manager, agent_manager_task)
        forwarder.cancel()


async def _forward_outbound(
    websocket: WebSocket,
    streaming_events: asyncio.Queue[StreamingEvent],
) -> None:
    """Sole writer to the websocket. Drains the agent activities queue and sends each message as JSON."""
    try:
        while True:
            message = await streaming_events.get()
            await websocket.send_json(message.model_dump(mode="json"))
    except (asyncio.CancelledError, WebSocketDisconnect):
        return


async def _stop_agent_manager(agent_manager: AgentManager, agent_manager_task: asyncio.Task[None]) -> None:
    """Stops the agent manager and waits for it to exit."""
    await agent_manager.shutdown()
    if agent_manager_task.done():
        return
    await agent_manager_task
