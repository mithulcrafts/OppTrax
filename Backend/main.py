import asyncio
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from config import (  # noqa: E402
    CORS_ORIGINS,
    ENABLE_BACKGROUND_TASKS,
    ROOT_DIR,
    SERVE_FRONTEND,
    integration_status,
)
from database import get_db_backend, init_database  # noqa: E402
from routers import auth, opportunities, whatsapp_webhook, yutori_webhook  # noqa: E402
from tasks.cron_jobs import daily_digest_loop, deadline_reminder_loop, yutori_polling_loop  # noqa: E402

FRONTEND_DIST = Path(ROOT_DIR) / "Frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("[SERVER] OppTrax Agentic Backend starting up...", flush=True)
    db_mode = await init_database()
    print(f"[SERVER] Database initialized in '{db_mode}' mode.", flush=True)

    bg_tasks = (
        [
            asyncio.create_task(deadline_reminder_loop()),
            asyncio.create_task(yutori_polling_loop()),
            asyncio.create_task(daily_digest_loop()),
        ]
        if ENABLE_BACKGROUND_TASKS
        else []
    )
    if not ENABLE_BACKGROUND_TASKS:
        print("[SERVER] Background polling loops disabled (ENABLE_BACKGROUND_TASKS=false).", flush=True)
    try:
        yield
    finally:
        if bg_tasks:
            print("[SERVER] Shutting down background tasks...", flush=True)
            for task in bg_tasks:
                task.cancel()
            await asyncio.gather(*bg_tasks, return_exceptions=True)


app = FastAPI(
    title="OppTrax Agentic Server",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(opportunities.router)
app.include_router(whatsapp_webhook.router)
app.include_router(yutori_webhook.router)


@app.get("/health")
@app.get("/api/health")
async def health_check() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "OppTrax Agentic Server",
        "database_backend": get_db_backend(),
        "integrations": integration_status(),
        "frontend_built": (FRONTEND_DIST / "index.html").exists(),
    }


if SERVE_FRONTEND and (FRONTEND_DIST / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIST / "assets")), name="frontend-assets")


@app.get("/")
async def root() -> Any:
    index_file = FRONTEND_DIST / "index.html"
    if SERVE_FRONTEND and index_file.is_file():
        return FileResponse(str(index_file))
    return JSONResponse(
        {
            "status": "ok",
            "message": "OppTrax Agentic Server is running. Visit /docs for API documentation.",
            "health": "/health",
        }
    )
