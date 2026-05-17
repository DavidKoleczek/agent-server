from fastapi import FastAPI, WebSocket
import uvicorn

app = FastAPI()


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    while True:
        data = await websocket.receive_text()
        await websocket.send_text(f"Message text was: {data}")


def run() -> None:
    uvicorn.run("agent_server.main:app", host="127.0.0.1", port=8000, reload=True)
