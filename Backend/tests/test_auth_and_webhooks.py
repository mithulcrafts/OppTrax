import hashlib
import hmac
import json

from config import VERIFY_TOKEN
from database import otp_verifications_collection, whatsapp_messages_collection


def test_health_endpoint(client):
    res = client.get("/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert str(body["database_backend"]).startswith("sqlite:")
    assert "integrations" in body


def test_otp_flow_and_bilingual_welcome(client):
    send_res = client.post("/api/auth/send-otp", json={"phone": "+91 98765 43210"})
    assert send_res.status_code == 200
    send_data = send_res.json()
    assert send_data["status"] in {"sandbox_mode", "success"}
    assert send_data["phone"] == "919876543210"

    import asyncio

    otp_rec = asyncio.run(otp_verifications_collection.find_one({"whatsapp_phone": "919876543210"}))
    assert otp_rec is not None
    code = otp_rec["otp"]

    verify_res = client.post(
        "/api/auth/verify-otp",
        json={"phone": "+91 98765 43210", "otp": code, "preferred_language": "both"},
    )
    assert verify_res.status_code == 200
    v_data = verify_res.json()
    assert v_data["status"] == "success"
    assert v_data["session_token"].startswith("919876543210.")
    assert v_data["user"]["preferred_language"] == "both"
    assert v_data["user"]["whatsapp_verified"] is True

    sess_res = client.get("/api/auth/session", headers={"X-Session-Token": v_data["session_token"]})
    assert sess_res.status_code == 200
    assert sess_res.json()["user"]["whatsapp_phone"] == "919876543210"

    msgs = asyncio.run(whatsapp_messages_collection.find({"whatsapp_phone": "919876543210"}).to_list(20))
    assert len(msgs) >= 2
    assert any("Welcome to OppTrax" in m["text"] and "स्वागत है" in m["text"] for m in msgs)


def test_otp_131030_sandbox_error(client, monkeypatch):
    async def fail_131030(to_phone, otp_code):
        raise RuntimeError("Meta API Error 131030: Recipient phone number not in allowed list")

    monkeypatch.setattr("routers.auth.send_otp_message", fail_131030)
    res = client.post("/api/auth/send-otp", json={"phone": "+919988776655"})
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "sandbox_error"
    assert data["detail"] == "allowed_list_error"


def test_whatsapp_webhook_verify_signature_and_dedup(client, monkeypatch):
    good = client.get(
        "/webhook",
        params={"hub.mode": "subscribe", "hub.challenge": "987654", "hub.verify_token": VERIFY_TOKEN},
    )
    assert good.status_code == 200
    assert good.text == "987654"

    bad = client.get(
        "/webhook",
        params={"hub.mode": "subscribe", "hub.challenge": "123", "hub.verify_token": "wrong_token"},
    )
    assert bad.status_code == 403

    monkeypatch.setattr("services.whatsapp_api.WHATSAPP_APP_SECRET", "test_secret_123")
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "id": "wamid.unique_1",
                                    "from": "919111222333",
                                    "type": "text",
                                    "text": {"body": "help"},
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }
    raw = json.dumps(payload).encode("utf-8")

    bad_sig = client.post(
        "/webhook",
        content=raw,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": "sha256=deadbeef"},
    )
    assert bad_sig.status_code == 403

    valid_sig = "sha256=" + hmac.new(b"test_secret_123", raw, hashlib.sha256).hexdigest()
    first = client.post(
        "/webhook",
        content=raw,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": valid_sig},
    )
    assert first.status_code == 200
    assert first.json()["results"][0]["action"] == "help"

    dup = client.post(
        "/webhook",
        content=raw,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": valid_sig},
    )
    assert dup.status_code == 200
    assert dup.json()["results"][0]["status"] == "duplicate"


def test_twilio_form_webhook(client):
    import asyncio
    from database import users_collection

    res = client.post(
        "/webhook",
        data={"From": "whatsapp:+919222333444", "Body": "lang hi", "MessageSid": "SM12345"},
    )
    assert res.status_code == 200
    assert "<Response></Response>" in res.text
    user = asyncio.run(users_collection.find_one({"whatsapp_phone": "919222333444"}))
    assert user is not None
    assert user["preferred_language"] == "hi"


def test_whatsapp_media_audio_and_image_ingestion(client, monkeypatch):
    import asyncio

    async def fake_download(media_id, ext="ogg"):
        return f"fake_{media_id}.{ext}"

    async def fake_transcribe(file_path):
        return {"transcript": "saved", "language_code": "en-IN"}

    async def fake_image_extract(file_path, mime_type="image/jpeg", caption=""):
        return [
            {
                "title": "Smart India Hackathon Poster",
                "title_hi": "स्मार्ट इंडिया हैकाथॉन",
                "organization": "MoE Innovation Cell",
                "category": "Hackathon",
                "location": "India",
                "stipend_or_reward": "INR 1,00,000 Prize",
                "eligibility": "College students across India",
                "application_steps": "Register through college SPOC",
                "summary_en": "- National coding hackathon with cash prizes.",
                "summary_hi": "- नकद पुरस्कारों के साथ राष्ट्रीय हैकाथॉन।",
                "url": "https://sih.gov.in",
                "deadline_iso": "2026-12-15T18:30:00Z",
                "semantic_relevance": 0.9,
            }
        ]

    monkeypatch.setattr("services.whatsapp_bot.download_whatsapp_media", fake_download)
    monkeypatch.setattr("services.whatsapp_bot.transcribe_voice_note", fake_transcribe)
    monkeypatch.setattr("services.whatsapp_bot.extract_opportunity_from_image", fake_image_extract)

    img_payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "id": "wamid.img_1",
                                    "from": "919333444555",
                                    "type": "image",
                                    "image": {"id": "media_img_1", "mime_type": "image/jpeg", "caption": "SIH poster"},
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }
    res_img = client.post("/webhook", json=img_payload)
    assert res_img.status_code == 200
    assert res_img.json()["results"][0]["action"] == "image_saved:1"

    audio_payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "id": "wamid.aud_1",
                                    "from": "919333444555",
                                    "type": "audio",
                                    "audio": {"id": "media_aud_1"},
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }
    res_aud = client.post("/webhook", json=audio_payload)
    assert res_aud.status_code == 200
    assert res_aud.json()["results"][0]["action"] == "board_listed"

    msgs = asyncio.run(whatsapp_messages_collection.find({"whatsapp_phone": "919333444555"}).to_list(20))
    assert any("Smart India Hackathon" in m["text"] for m in msgs)
