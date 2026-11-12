import asyncio
import time
from datetime import datetime, timezone
from typing import Any

from config import DAILY_DIGEST_HOUR_UTC, REMINDER_CHECK_INTERVAL_SECONDS, YUTORI_POLL_INTERVAL_SECONDS
from database import tasks_collection, users_collection
from routers.yutori_webhook import normalize_yutori_payload, process_yutori_findings_batch
from services.live_scout import search_live_opportunities
from services.opportunity_service import run_deadline_reminder_sweep, save_and_notify_opportunity, send_daily_digest_for_user
from services.yutori_client import fetch_yutori_updates, get_yutori_research_status


async def _poll_livescout_task(task: dict[str, Any]) -> int:
    task_id = str(task.get("yutori_task_id") or "")
    phone = str(task.get("whatsapp_phone") or "")
    query = str(task.get("query_instruction") or "")
    if not phone or not query:
        return 0

    user = await users_collection.find_one({"whatsapp_phone": phone}) or {}
    live_res = await search_live_opportunities(
        query_text=query,
        user_profile=user.get("profile_json"),
        learned_preferences=user.get("learned_preferences"),
        limit=4,
    )
    items = live_res.get("items") or []
    citations = live_res.get("citations") or []

    saved_count = 0
    for item in items:
        doc = await save_and_notify_opportunity(
            parsed=item,
            owner_phone=phone,
            raw_content=item.get("full_details_en", item.get("summary_en", "")),
            task_id=task_id,
            task_type=str(task.get("task_type") or "CAREER"),
            citations=citations,
            notify_whatsapp=True,
        )
        if doc:
            saved_count += 1

    await tasks_collection.update_one({"yutori_task_id": task_id}, {"$set": {"last_checked": time.time()}})
    return saved_count


async def poll_single_yutori_task(task: dict[str, Any]) -> int:
    """Polls one active LiveScout or legacy remote scout/research task and processes new findings."""
    task_id = str(task.get("yutori_task_id") or "")
    if not task_id:
        return 0
    if task_id.startswith(("livescout_", "local_")):
        if task.get("task_kind") == "research":
            return 0
        return await _poll_livescout_task(task)

    task_kind = str(task.get("task_kind") or "scout")
    payload = await (get_yutori_research_status(task_id) if task_kind == "research" else fetch_yutori_updates(task_id))
    if not isinstance(payload, dict):
        return 0

    normalized = normalize_yutori_payload({**payload, "task_id": task_id})
    status_lower = normalized["status"].lower()

    if task_kind == "research" and status_lower in {"failed", "error", "cancelled"}:
        await tasks_collection.update_one({"yutori_task_id": task_id}, {"$set": {"status": "FAILED"}})
        return 0

    saved_count = 0
    if normalized["raw_text"]:
        saved = await process_yutori_findings_batch(
            task_doc=task,
            raw_text=normalized["raw_text"],
            citations=normalized["citations"],
            notify_whatsapp=True,
        )
        saved_count = len(saved)

    await tasks_collection.update_one(
        {"yutori_task_id": task_id},
        {"$set": {"last_checked": time.time()}},
    )
    return saved_count


async def deadline_reminder_loop() -> None:
    """Background loop checking tracked deadlines and sending tiered WhatsApp alerts."""
    await asyncio.sleep(5)
    while True:
        try:
            sent = await run_deadline_reminder_sweep()
            if sent:
                print(f"[CRON REMINDERS] Sent {sent} deadline reminder(s).", flush=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[CRON REMINDERS ERROR] {exc}", flush=True)
        await asyncio.sleep(REMINDER_CHECK_INTERVAL_SECONDS)


async def yutori_polling_loop() -> None:
    """Background loop polling Yutori Scouting and Research APIs as webhook fallback."""
    await asyncio.sleep(10)
    while True:
        try:
            cursor = tasks_collection.find({"status": "ACTIVE"})
            active_tasks = await cursor.to_list(length=100)
            for task in active_tasks:
                await poll_single_yutori_task(task)
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[YUTORI POLLER ERROR] {exc}", flush=True)
        await asyncio.sleep(YUTORI_POLL_INTERVAL_SECONDS)


async def daily_digest_loop() -> None:
    """Background loop delivering daily digest bundles to users in 'daily' digest mode."""
    await asyncio.sleep(15)
    while True:
        try:
            now_utc = datetime.now(timezone.utc)
            today_str = now_utc.strftime("%Y-%m-%d")
            if now_utc.hour >= DAILY_DIGEST_HOUR_UTC:
                users = await users_collection.find({"digest_mode": "daily"}).to_list(length=500)
                for user in users:
                    if user.get("last_digest_date") == today_str:
                        continue
                    phone = user.get("whatsapp_phone")
                    if phone:
                        await send_daily_digest_for_user(phone)
                        await users_collection.update_one(
                            {"whatsapp_phone": phone},
                            {"$set": {"last_digest_date": today_str}},
                        )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[DAILY DIGEST ERROR] {exc}", flush=True)
        await asyncio.sleep(1800)
