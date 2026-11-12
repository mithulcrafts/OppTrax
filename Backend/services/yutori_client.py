import asyncio
import uuid
from typing import Any

import requests

from config import ENABLE_YUTORI, SCOUT_PROVIDER, YUTORI_BASE_URL, YUTORI_KEY, get_yutori_webhook_base_url


def _yutori_cloud_enabled() -> bool:
    return bool(ENABLE_YUTORI and SCOUT_PROVIDER != "livescout" and YUTORI_KEY)


def _launch_scout_sync(instruction: str, output_interval: int = 3600) -> str | None:
    if not _yutori_cloud_enabled():
        return f"livescout_{uuid.uuid4().hex[:12]}"
    url = f"{YUTORI_BASE_URL}/scouting/tasks"
    headers = {"X-API-Key": YUTORI_KEY, "Content-Type": "application/json"}
    data: dict[str, Any] = {
        "query": instruction,
        "output_interval": max(1800, int(output_interval)),
    }
    webhook_base = get_yutori_webhook_base_url()
    if webhook_base:
        data["webhook_url"] = f"{webhook_base}/yutori-webhook"

    print(f"[YUTORI] Dispatching cloud scout for: {instruction[:120]}...", flush=True)
    try:
        res = requests.post(url, json=data, headers=headers, timeout=15)
        if res.status_code in (200, 201):
            payload = res.json()
            return payload.get("id") or payload.get("task_id")
        print(f"[YUTORI ERROR] HTTP {res.status_code}: {res.text}", flush=True)
    except Exception as exc:
        print(f"[YUTORI ERROR] {exc}", flush=True)
    return f"livescout_{uuid.uuid4().hex[:12]}"


async def launch_yutori_scout(instruction: str, output_interval: int = 3600) -> str | None:
    return await asyncio.to_thread(_launch_scout_sync, instruction, output_interval)


def _terminate_scout_sync(task_id: str) -> bool:
    if str(task_id).startswith(("livescout_", "local_")) or not _yutori_cloud_enabled():
        return True
    print(f"[YUTORI] Sending termination signal for Task ID: {task_id}", flush=True)
    url = f"{YUTORI_BASE_URL}/scouting/tasks/{task_id}"
    headers = {"X-API-Key": YUTORI_KEY}
    try:
        res = requests.delete(url, headers=headers, timeout=12)
        if res.status_code not in (200, 202, 204):
            print(f"[YUTORI STOP ERROR] HTTP {res.status_code}: {res.text}", flush=True)
            if res.status_code in (403, 404):
                return True
        return res.status_code in (200, 202, 204)
    except Exception as exc:
        print(f"[YUTORI STOP ERROR] {exc}", flush=True)
        return False


async def terminate_yutori_scout(task_id: str) -> bool:
    return await asyncio.to_thread(_terminate_scout_sync, task_id)


def _fetch_updates_sync(task_id: str) -> dict[str, Any]:
    if str(task_id).startswith(("livescout_", "local_")) or not _yutori_cloud_enabled():
        return {"updates": []}
    url = f"{YUTORI_BASE_URL}/scouting/tasks/{task_id}/updates?page_size=10"
    headers = {"X-API-Key": YUTORI_KEY}
    try:
        res = requests.get(url, headers=headers, timeout=15)
        if res.status_code == 200:
            payload = res.json()
            if isinstance(payload, dict):
                return payload
            if isinstance(payload, list):
                return {"updates": payload}
            return {"updates": []}
        print(f"[YUTORI POLL ERROR] HTTP {res.status_code}: {res.text}", flush=True)
    except Exception as exc:
        print(f"[YUTORI POLL ERROR] Error fetching updates: {exc}", flush=True)
    return {"updates": []}


async def fetch_yutori_updates(task_id: str) -> dict[str, Any]:
    return await asyncio.to_thread(_fetch_updates_sync, task_id)


def _launch_research_sync(query: str, output_schema: dict[str, Any] | None = None) -> str | None:
    if not _yutori_cloud_enabled():
        return f"livescout_res_{uuid.uuid4().hex[:10]}"
    url = f"{YUTORI_BASE_URL}/research/tasks"
    headers = {"X-API-Key": YUTORI_KEY, "Content-Type": "application/json"}
    data: dict[str, Any] = {
        "query": query,
        "user_timezone": "Asia/Kolkata",
    }
    if output_schema:
        data["output_schema"] = output_schema
    webhook_base = get_yutori_webhook_base_url()
    if webhook_base:
        data["webhook_url"] = f"{webhook_base}/yutori-webhook"

    print(f"[YUTORI RESEARCH] Dispatching research task: {query[:120]}...", flush=True)
    try:
        res = requests.post(url, json=data, headers=headers, timeout=15)
        if res.status_code in (200, 201):
            payload = res.json()
            if isinstance(payload, dict):
                task_id = payload.get("id") or payload.get("task_id")
                return str(task_id) if task_id else None
            return None
        print(f"[YUTORI RESEARCH ERROR] HTTP {res.status_code}: {res.text}", flush=True)
    except Exception as exc:
        print(f"[YUTORI RESEARCH ERROR] {exc}", flush=True)
    return f"livescout_res_{uuid.uuid4().hex[:10]}"


async def launch_yutori_research(query: str, output_schema: dict[str, Any] | None = None) -> str | None:
    return await asyncio.to_thread(_launch_research_sync, query, output_schema)


def _get_research_status_sync(task_id: str) -> dict[str, Any] | None:
    if not YUTORI_KEY:
        return None
    url = f"{YUTORI_BASE_URL}/research/tasks/{task_id}"
    headers = {"X-API-Key": YUTORI_KEY}
    try:
        res = requests.get(url, headers=headers, timeout=15)
        if res.status_code == 200:
            return res.json()
        print(f"[YUTORI RESEARCH STATUS ERROR] HTTP {res.status_code}: {res.text}", flush=True)
    except Exception as exc:
        print(f"[YUTORI RESEARCH STATUS ERROR] {exc}", flush=True)
    return None


async def get_yutori_research_status(task_id: str) -> dict[str, Any] | None:
    return await asyncio.to_thread(_get_research_status_sync, task_id)
