from fastapi import FastAPI

from agent_server.routes.agent import router as agent_router
from agent_server.routes.health import router as health_router
from agent_server.routes.resume import router as resume_router

app = FastAPI()
app.include_router(agent_router)
app.include_router(health_router)
app.include_router(resume_router)
