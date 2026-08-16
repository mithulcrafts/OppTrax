import uvicorn
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI

from tasks.cron_jobs import deadline_reminder_loop, yutori_polling_loop
from routers import whatsapp_webhook, yutori_webhook

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start the background checking loops when the server boots
    reminder_task = asyncio.create_task(deadline_reminder_loop())
    polling_task = asyncio.create_task(yutori_polling_loop())
    yield
    reminder_task.cancel()
    polling_task.cancel()

app = FastAPI(lifespan=lifespan)

# Register Routers
app.include_router(whatsapp_webhook.router)
app.include_router(yutori_webhook.router)

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
