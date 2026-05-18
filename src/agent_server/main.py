from fastapi import FastAPI
import uvicorn

from agent_server.routes.agent import router as agent_router

app = FastAPI()
app.include_router(agent_router)


def run() -> None:
    uvicorn.run("agent_server.main:app", host="127.0.0.1", port=8000, reload=True)
