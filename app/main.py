from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator
from uuid import uuid4

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.core.config import get_settings
from app.core.logger import bind_trace, configure_logging
from app.services import MemoryStore, Orchestrator, TaskManager, TaskQueue


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await app.state.task_manager.recover_interrupted_tasks()
    await app.state.task_queue.start()
    try:
        yield
    finally:
        await app.state.task_queue.stop()
        await app.state.orchestrator.close()


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings)

    task_manager = TaskManager(settings)
    memory = MemoryStore(settings)
    orchestrator = Orchestrator(settings, task_manager, memory)
    task_queue = TaskQueue(settings, orchestrator)

    app = FastAPI(
        title=settings.app_name,
        version="1.0.0",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.task_manager = task_manager
    app.state.memory = memory
    app.state.orchestrator = orchestrator
    app.state.task_queue = task_queue
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def request_trace_middleware(request, call_next):
        request_id = request.headers.get("x-request-id", str(uuid4()))
        bind_trace(request_id=request_id)
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        return response

    app.include_router(router)
    app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
    return app


app = create_app()
