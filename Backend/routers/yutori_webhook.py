import hashlib
import hmac
import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from config import YUTORI_WEBHOOK_SECRET
from database import processed_webhooks_collection, tasks_collection, users_collection
from services.ai_engine import parse_yutori_findings
from services.language import normalize_language
from services.opportunity_service import save_and_notify_opportunity
from services.whatsapp_api import send_whatsapp_message

router = APIRouter()


def _collect_from_updates(updates: Any, text_blocks: list[str], citations: list[dict[str, Any]]) -> None:
    if not isinstance(updates, list):
        return
    for upd in updates:
        if not isinstance(upd, dict):
            continue
        content = upd.get("content") or upd.get("summary") or ""
        if content:
            text_blocks.append(str(content))
        if upd.get("structured_result") is not None:
            text_blocks.append(json.dumps(upd["structured_result"], ensure_ascii=False))
        upd_cits = upd.get("citations")
        if isinstance(upd_cits, list):
            citations.extend(c for c in upd_cits if isinstance(c, dict))


def _collect_from_findings(findings: Any, text_blocks: list[str]) -> None:
    if isinstance(findings, list):
        for item in findings:
            if isinstance(item, dict):
                text_blocks.append(json.dumps(item, ensure_ascii=False))
            elif item:
                text_blocks.append(str(item))
    elif isinstance(findings, str) and findings.strip():
        text_blocks.append(findings.strip())


def _dedupe_citations(citations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen_urls: set[str] = set()
    unique: list[dict[str, Any]] = []
    for cit in citations:
        url = str(cit.get("url") or "").strip()
        if url and url not in seen_urls:
            seen_urls.add(url)
            unique.append(cit)
    return unique


def normalize_yutori_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalizes scouting updates, research callbacks, and legacy webhook bodies."""
    nested = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    task_id = payload.get("task_id") or payload.get("scout_id") or payload.get("id") or nested.get("task_id")

    citations: list[dict[str, Any]] = []
    if isinstance(payload.get("citations"), list):
        citations.extend(c for c in payload["citations"] if isinstance(c, dict))

    text_blocks: list[str] = []
    _collect_from_updates(payload.get("updates"), text_blocks, citations)
    _collect_from_findings(payload.get("findings"), text_blocks)

    for key in ("content", "result", "summary", "text", "structured_result"):
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            text_blocks.append(val.strip())
        elif isinstance(val, (dict, list)) and val:
            text_blocks.append(json.dumps(val, ensure_ascii=False))

    if not text_blocks and payload:
        text_blocks.append(json.dumps(payload, ensure_ascii=False))

    return {
        "task_id": str(task_id) if task_id else None,
        "status": str(payload.get("status") or ""),
        "raw_text": "\n\n".join(text_blocks).strip(),
        "citations": _dedupe_citations(citations),
    }


def _research_header(preferred_lang: str) -> str:
    if preferred_lang == "hi":
        return "*यूटोरी डीप रिसर्च रिपोर्ट*"
    if preferred_lang == "en":
        return "*Yutori Deep Research Report*"
    return "*Yutori Deep Research Complete / यूटोरी डीप रिसर्च रिपोर्ट*"


async def process_yutori_findings_batch(
    task_doc: dict[str, Any],
    raw_text: str,
    citations: list[dict[str, Any]] | None = None,
    notify_whatsapp: bool = True,
) -> list[dict[str, Any]]:
    """Parses Yutori output with Gemini, deduplicates updates, scores, stores, and notifies."""
    if not raw_text or not raw_text.strip():
        return []

    task_id = str(task_doc.get("yutori_task_id") or task_doc.get("_id") or "unknown")
    content_hash = hashlib.md5(f"{task_id}:{raw_text[:8000]}".encode("utf-8")).hexdigest()
    dedup_id = f"yutori_{content_hash}"

    if await processed_webhooks_collection.find_one({"_id": dedup_id}):
        return []

    owner_phone = str(task_doc.get("whatsapp_phone") or "")
    task_type = str(task_doc.get("task_type") or "CAREER")
    task_kind = str(task_doc.get("task_kind") or "scout")

    user = await users_collection.find_one({"whatsapp_phone": owner_phone}) if owner_phone else None
    preferred_lang = normalize_language((user or {}).get("preferred_language"), default="both")

    parsed_items = await parse_yutori_findings(
        raw_findings=raw_text,
        task_type=task_type,
        user_profile=(user or {}).get("profile_json"),
        learned_preferences=(user or {}).get("learned_preferences"),
        citations=citations,
    )

    if task_kind == "research" and notify_whatsapp and owner_phone:
        await send_whatsapp_message(owner_phone, f"{_research_header(preferred_lang)}\n\n{raw_text[:3400]}")

    saved_docs: list[dict[str, Any]] = []
    for item in parsed_items:
        doc = await save_and_notify_opportunity(
            parsed=item,
            owner_phone=owner_phone,
            raw_content=raw_text,
            task_id=task_id,
            task_type=task_type,
            citations=citations,
            notify_whatsapp=notify_whatsapp,
        )
        if doc:
            saved_docs.append(doc)

    await processed_webhooks_collection.update_one(
        {"_id": dedup_id},
        {"$set": {"task_id": task_id, "processed_at": datetime.now(timezone.utc).isoformat()}},
        upsert=True,
    )

    if task_kind == "research":
        await tasks_collection.update_one(
            {"yutori_task_id": task_id},
            {"$set": {"status": "COMPLETED", "last_checked": datetime.now(timezone.utc).timestamp()}},
        )

    return saved_docs


def _verify_yutori_signature(request: Request, raw_body: bytes) -> bool:
    if not YUTORI_WEBHOOK_SECRET:
        return True
    sig_header = request.headers.get("X-Yutori-Signature") or request.headers.get("X-Webhook-Secret") or ""
    auth_header = request.headers.get("Authorization") or ""
    bearer = auth_header[7:].strip() if auth_header.lower().startswith("bearer ") else ""
    expected_hex = hmac.new(YUTORI_WEBHOOK_SECRET.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()

    for candidate in (sig_header.strip(), bearer):
        if not candidate:
            continue
        clean = candidate.split("=", 1)[1].strip() if candidate.lower().startswith("sha256=") else candidate
        if hmac.compare_digest(clean, YUTORI_WEBHOOK_SECRET) or hmac.compare_digest(clean, expected_hex):
            return True
    return False


@router.post("/yutori-webhook")
async def yutori_webhook_handler(request: Request) -> dict[str, Any]:
    """Handles real-time webhook callbacks from Yutori Scouting and Research APIs."""
    raw_body = await request.body()
    if not _verify_yutori_signature(request, raw_body):
        raise HTTPException(status_code=403, detail="Invalid Yutori webhook secret")

    try:
        payload = json.loads(raw_body.decode("utf-8")) if raw_body else None
    except Exception:
        return {"status": "ignored", "reason": "invalid_json"}

    if not isinstance(payload, dict):
        return {"status": "ignored", "reason": "invalid_payload"}

    normalized = normalize_yutori_payload(payload)
    task_id = normalized["task_id"]
    print(f"[YUTORI WEBHOOK] Received update for task: {task_id}", flush=True)

    task_doc = await tasks_collection.find_one({"yutori_task_id": task_id}) if task_id else None
    if not task_doc:
        print(f"[YUTORI WEBHOOK] Task {task_id} not found in DB; skipping.", flush=True)
        return {"status": "ignored", "reason": "task_not_found"}

    saved_docs = await process_yutori_findings_batch(
        task_doc=task_doc,
        raw_text=normalized["raw_text"],
        citations=normalized["citations"],
        notify_whatsapp=True,
    )

    return {
        "status": "processed",
        "task_id": task_id,
        "saved_count": len(saved_docs),
        "saved_ids": [d["_id"] for d in saved_docs],
    }
