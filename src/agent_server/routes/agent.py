import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from openai.types.responses import ResponseTextDeltaEvent
from pydantic import TypeAdapter, ValidationError

from agent_server.schemas.activity import (
    AssistantActivity,
    CancelActivity,
    ClientActivity,
    QuitActivity,
    ToolActivity,
    UserActivity,
)

router = APIRouter()

_CLIENT_ACTIVITY_ADAPTER = TypeAdapter(ClientActivity)

_STREAM_DELAY_SECONDS = 0.2


@router.websocket("/agent")
async def agent_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                activity = _CLIENT_ACTIVITY_ADAPTER.validate_json(raw)
            except ValidationError as exc:
                await websocket.send_json({"type": "error", "detail": exc.errors()})
                continue

            match activity:
                case UserActivity(content=content):
                    await _stream_response(websocket, content)
                case CancelActivity():
                    # Hard-coded responses are sent synchronously above, so there is nothing in-flight to cancel right now.
                    await websocket.send_json({"type": "cancelled"})
                case QuitActivity():
                    await websocket.close()
                    return
    except WebSocketDisconnect:
        return


def _hardcoded_response(prompt: str) -> list[AssistantActivity | ToolActivity]:
    deltas = ["Hello! ", "You said: ", f"{prompt!r}. ", "Goodbye."]
    text_events: list[AssistantActivity | ToolActivity] = [
        ResponseTextDeltaEvent(
            type="response.output_text.delta",
            delta=delta,
            item_id="msg_demo",
            output_index=0,
            content_index=0,
            sequence_number=index,
            logprobs=[],
        )
        for index, delta in enumerate(deltas)
    ]
    tool_event = ToolActivity(
        type="tool",
        tool_name="echo",
        tool_arguments={"input": prompt},
        tool_output=prompt,
    )
    return [*text_events, tool_event]


async def _stream_response(websocket: WebSocket, prompt: str) -> None:
    for event in _hardcoded_response(prompt):
        await websocket.send_text(event.model_dump_json())
        await asyncio.sleep(_STREAM_DELAY_SECONDS)
