from fastapi import FastAPI
import uvicorn

from agent_server.routes.activity import router as activity_router

app = FastAPI()
app.include_router(activity_router)


def run() -> None:
    uvicorn.run("agent_server.main:app", host="127.0.0.1", port=8000, reload=True)
