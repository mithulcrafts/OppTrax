import json
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, Response

from config import VERIFY_TOKEN
from routers.auth import sanitize_phone_number
from services.whatsapp_api import verify_webhook_signature
from services.whatsapp_bot import handle_incoming_message

router = APIRouter()


def _normalize_phone(raw: str | None) -> str:
    if not raw:
        return ""
    cleaned = str(raw).replace("whatsapp:", "").strip()
    return sanitize_phone_number(cleaned)


def _extract_meta_message_fields(message: dict[str, Any]) -> dict[str, Any]:
    msg_type = str(message.get("type") or "text")
    text = None
    interactive_id = None
    media_id = None
    mime_type = None
    caption = ""

    if msg_type == "text":
        text = (message.get("text") or {}).get("body")
    elif msg_type == "interactive":
        interactive = message.get("interactive") or {}
        itype = interactive.get("type")
        if itype == "button_reply":
            reply = interactive.get("button_reply") or {}
            interactive_id = reply.get("id")
        elif itype == "list_reply":
            reply = interactive.get("list_reply") or {}
            interactive_id = reply.get("id")
    elif msg_type == "button":
        btn = message.get("button") or {}
        interactive_id = btn.get("payload") or btn.get("text")
    elif msg_type in {"audio", "document", "image"}:
        media_obj = message.get(msg_type) or {}
        media_id = media_obj.get("id")
        mime_type = media_obj.get("mime_type")
        caption = str(media_obj.get("caption") or "")

    return {
        "message_id": message.get("id"),
        "from_phone": _normalize_phone(message.get("from")),
        "msg_type": msg_type,
        "text": text,
        "interactive_id": interactive_id,
        "media_id": media_id,
        "mime_type": mime_type,
        "caption": caption,
    }


def _extract_meta_messages(payload: dict[str, Any]) -> list[dict[str, Any]]:
    extracted: list[dict[str, Any]] = []
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            for msg in value.get("messages") or []:
                if isinstance(msg, dict):
                    extracted.append(_extract_meta_message_fields(msg))
    return extracted


@router.get("/webhook")
async def verify_whatsapp_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
) -> Response:
    if hub_mode == "subscribe" and hub_verify_token == VERIFY_TOKEN and hub_challenge is not None:
        print("[WEBHOOK VERIFY] Verification successful!", flush=True)
        return Response(content=str(hub_challenge), media_type="text/plain")
    print("[WEBHOOK VERIFY] Failed token check!", flush=True)
    raise HTTPException(status_code=403, detail="Invalid verification token")


@router.post("/webhook")
async def whatsapp_webhook_handler(request: Request) -> Any:
    content_type = request.headers.get("content-type", "").lower()

    # Optional Twilio WhatsApp webhook support
    if "application/x-www-form-urlencoded" in content_type:
        form = await request.form()
        from_phone = _normalize_phone(form.get("From"))
        body_text = str(form.get("Body") or "")
        msg_sid = str(form.get("MessageSid") or "")
        if from_phone and body_text:
            await handle_incoming_message(
                from_phone=from_phone,
                text=body_text,
                msg_type="text",
                message_id=msg_sid or None,
            )
        return Response(content="<Response></Response>", media_type="application/xml")

    raw_body = await request.body()
    sig_header = request.headers.get("X-Hub-Signature-256")
    if not verify_webhook_signature(raw_body, sig_header):
        raise HTTPException(status_code=403, detail="Invalid WhatsApp webhook signature")

    try:
        payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from exc

    events = _extract_meta_messages(payload) if isinstance(payload, dict) else []
    results = []
    for ev in events:
        if not ev["from_phone"]:
            continue
        res = await handle_incoming_message(
            from_phone=ev["from_phone"],
            text=ev["text"],
            interactive_id=ev["interactive_id"],
            msg_type=ev["msg_type"],
            media_id=ev["media_id"],
            mime_type=ev["mime_type"],
            caption=ev["caption"],
            message_id=ev["message_id"],
        )
        results.append(res)

    return {"status": "ok", "processed": len(results), "results": results}
