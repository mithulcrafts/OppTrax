import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

os.environ.setdefault("USE_LOCAL_DB", "true")
os.environ.setdefault("DATABASE_BACKEND", "sqlite")
os.environ.setdefault("SERVE_FRONTEND", "false")
os.environ.setdefault("ENABLE_BACKGROUND_TASKS", "false")

from database import use_local_database  # noqa: E402
from main import app  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch):
    use_local_database(":memory:")
    monkeypatch.setattr("services.whatsapp_api.WHATSAPP_TOKEN", None)
    monkeypatch.setattr("services.whatsapp_api.PHONE_NUMBER_ID", None)
    monkeypatch.setattr("services.whatsapp_api.WHATSAPP_APP_SECRET", None)
    monkeypatch.setattr("routers.yutori_webhook.YUTORI_WEBHOOK_SECRET", None)
    monkeypatch.setattr("services.yutori_client.YUTORI_KEY", None)
    monkeypatch.setattr("services.ai_engine.gemini_client", None)
    monkeypatch.setattr("services.media_processor.gemini_client", None)
    monkeypatch.setattr("services.media_processor.sarvam_client", None)
    monkeypatch.setattr("config.sarvam_client", None)
    monkeypatch.setattr("services.live_scout.LIVE_SCOUT_NETWORK_ENABLED", False)
    yield


@pytest.fixture()
def client():
    with TestClient(app) as test_client:
        yield test_client
