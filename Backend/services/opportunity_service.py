import hashlib
import re
import time
from datetime import datetime, timezone
from typing import Any

from database import (
    opportunities_collection,
    tasks_collection,
    tracked_collection,
    users_collection,
)
from services.whatsapp_api import (
    send_deadline_reminder_card,
    send_opportunity_card,
    send_whatsapp_message,
)


def parse_deadline_dt(deadline_iso: str | None) -> datetime | None:
    if not deadline_iso:
        return None
    raw = str(deadline_iso).strip()
    if raw.lower() in {"null", "none", "n/a", ""}:
        return None
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
            raw = f"{raw}T23:59:59+00:00"
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def is_valid_opportunity(title: str, summary: str, url: str) -> bool:
    title_lower = title.lower()
    summary_lower = summary.lower()
    url_lower = url.lower()

    if not url_lower.startswith("http"):
        return False

    invalid_url_patterns = [
        "n/a",
        "null",
        "none",
        "link-if-available",
        "example.com",
        "placeholder",
        "your-link",
        "link_here",
        "url_here",
        "google.com/search",
    ]
    if any(pat in url_lower for pat in invalid_url_patterns):
        return False

    empty_phrases = [
        "no opportunities",
        "no upcoming",
        "no active",
        "no listings",
        "no jobs",
        "no internships",
        "no hackathons",
        "not found",
        "no match",
        "none found",
        "zero opportunities",
        "no results",
        "no postings",
        "not available",
        "no matching",
        "no new updates",
    ]
    if any(phrase in title_lower for phrase in empty_phrases):
        return False
    if len(summary) < 200 and any(phrase in summary_lower for phrase in empty_phrases):
        return False

    if title_lower in {"new update", "placeholder", "untitled", "test"}:
        return False

    return True


def _extract_keywords(opp: dict[str, Any]) -> list[str]:
    text = f"{opp.get('title', '')} {opp.get('category', '')} {opp.get('organization', '')}".lower()
    stop_words = {"the", "and", "for", "with", "from", "this", "that", "2026", "2027", "new", "open", "apply"}
    words = [w for w in re.findall(r"[a-z0-9+#]{3,}", text) if w not in stop_words]
    seen = []
    for w in words:
        if w not in seen:
            seen.append(w)
    return seen[:10]


def compute_priority_score(
    parsed: dict[str, Any],
    task_type: str,
    learned_preferences: dict[str, Any] | None = None,
) -> tuple[float, str]:
    s_relevance = float(parsed.get("semantic_relevance") or 0.65)
    s_relevance = max(0.0, min(1.0, s_relevance))

    s_urgency = 0.5
    deadline_dt = parse_deadline_dt(parsed.get("deadline_iso"))
    if deadline_dt:
        days_left = max(0.0, (deadline_dt - datetime.now(timezone.utc)).total_seconds() / 86400.0)
        if days_left <= 2:
            s_urgency = 1.0
        elif days_left <= 7:
            s_urgency = 0.85
        elif days_left <= 30:
            s_urgency = 0.6
        else:
            s_urgency = 0.3

    learning_delta = 0.0
    if learned_preferences:
        haystack = f"{parsed.get('title', '')} {parsed.get('summary_en', '')} {parsed.get('category', '')}".lower()
        liked = [k.lower() for k in learned_preferences.get("liked_keywords", [])]
        disliked = [k.lower() for k in learned_preferences.get("disliked_keywords", [])]
        like_hits = sum(1 for k in liked if k in haystack)
        dislike_hits = sum(1 for k in disliked if k in haystack)
        learning_delta = min(0.15, like_hits * 0.04) - min(0.25, dislike_hits * 0.08)

    if task_type == "CAREER":
        base_score = (0.65 * s_relevance) + (0.35 * s_urgency)
    else:
        base_score = (0.75 * s_relevance) + (0.25 * s_urgency)

    final_score = round(max(0.05, min(1.0, base_score + learning_delta)), 3)
    raw_reason = parsed.get("reasoning") or "Matches your search & profile preferences."
    reason = f"Rel: {int(s_relevance * 100)}% | Urg: {int(s_urgency * 100)}% — {raw_reason}"
    return final_score, reason


async def save_and_notify_opportunity(
    parsed: dict[str, Any],
    owner_phone: str,
    raw_content: str = "",
    task_id: str | None = None,
    task_type: str = "CAREER",
    citations: list[dict[str, Any]] | None = None,
    notify_whatsapp: bool = True,
    force_immediate: bool = False,
) -> dict[str, Any] | None:
    title_str = str(parsed.get("title") or "New Opportunity").strip()
    url_str = str(parsed.get("url") or "N/A").strip()
    summary_en = str(parsed.get("summary_en") or parsed.get("summary") or "").strip()
    summary_hi = str(parsed.get("summary_hi") or "").strip()

    if not is_valid_opportunity(title_str, summary_en, url_str):
        return None

    dedup_scope = task_id or owner_phone or "global"
    finding_id_src = f"{dedup_scope}_{title_str.lower()}_{url_str.lower()}"
    finding_id = hashlib.md5(finding_id_src.encode("utf-8")).hexdigest()[:12]

    existing_opp = await opportunities_collection.find_one({"_id": finding_id})
    if existing_opp:
        return None

    user = await users_collection.find_one({"whatsapp_phone": owner_phone}) if owner_phone else None
    preferred_language = (user or {}).get("preferred_language", "both")
    learned_preferences = (user or {}).get("learned_preferences", {})
    digest_mode = (user or {}).get("digest_mode", "instant")

    final_priority_score, reasoning_line = compute_priority_score(
        parsed,
        task_type=task_type,
        learned_preferences=learned_preferences,
    )

    deadline_iso = parsed.get("deadline_iso")
    deadline_dt = parse_deadline_dt(deadline_iso)
    deadline_ts = deadline_dt.timestamp() if deadline_dt else None

    opp_doc = {
        "_id": finding_id,
        "yutori_task_id": task_id,
        "whatsapp_phone": owner_phone,
        "raw_content": raw_content[:16000] if raw_content else summary_en,
        "title": title_str,
        "title_hi": str(parsed.get("title_hi") or title_str).strip(),
        "organization": parsed.get("organization") or "Not specified",
        "category": parsed.get("category") or task_type,
        "location": parsed.get("location") or "Not specified",
        "stipend_or_reward": parsed.get("stipend_or_reward") or "Not specified",
        "eligibility": parsed.get("eligibility") or "See official link",
        "application_steps": parsed.get("application_steps") or "Apply via official link",
        "url": url_str,
        "summary": summary_en,
        "summary_en": summary_en,
        "summary_hi": summary_hi,
        "full_details_en": str(parsed.get("full_details_en") or summary_en),
        "full_details_hi": str(parsed.get("full_details_hi") or summary_hi or summary_en),
        "deadline_iso": deadline_dt.isoformat() if deadline_dt else deadline_iso,
        "deadline_timestamp": deadline_ts,
        "priority_score": final_priority_score,
        "semantic_relevance": parsed.get("semantic_relevance"),
        "reasoning": reasoning_line,
        "citations": citations or [],
        "digested": False,
        "created_at": time.time(),
    }

    await opportunities_collection.insert_one(opp_doc)

    if task_id:
        finding_entry = {
            "finding_id": finding_id,
            "text": f"[{title_str}] {summary_en} ({url_str})",
            "received_at": datetime.now(timezone.utc).isoformat(),
        }
        await tasks_collection.update_one(
            {"yutori_task_id": task_id},
            {"$push": {"findings": finding_entry}},
        )

    should_push = notify_whatsapp and owner_phone and (force_immediate or digest_mode != "daily")
    if should_push:
        await send_opportunity_card(
            to_phone=owner_phone,
            finding_id=finding_id,
            title=title_str,
            summary=summary_en,
            summary_hi=summary_hi,
            title_hi=opp_doc["title_hi"],
            organization=opp_doc["organization"],
            location=opp_doc["location"],
            stipend_or_reward=opp_doc["stipend_or_reward"],
            eligibility=opp_doc["eligibility"],
            application_steps=opp_doc["application_steps"],
            full_details_en=opp_doc["full_details_en"],
            full_details_hi=opp_doc["full_details_hi"],
            url=url_str,
            task_type=opp_doc["category"],
            priority_score=final_priority_score,
            reasoning=reasoning_line,
            deadline=opp_doc["deadline_iso"],
            preferred_language=preferred_language,
        )
        await opportunities_collection.update_one({"_id": finding_id}, {"$set": {"digested": True}})

    return opp_doc


async def track_opportunity_for_user(
    phone: str,
    opp_id: str,
    status: str = "interested",
    reminder_hours: list[int] | None = None,
) -> dict[str, Any] | None:
    opp = await opportunities_collection.find_one({"_id": opp_id})
    if not opp:
        return None

    await users_collection.update_one(
        {"whatsapp_phone": phone},
        {"$addToSet": {"saved_opportunities": opp_id}},
    )

    deadline_iso = opp.get("deadline_iso")
    deadline_dt = parse_deadline_dt(deadline_iso)
    deadline_ts = deadline_dt.timestamp() if deadline_dt else opp.get("deadline_timestamp")

    tiers = reminder_hours if reminder_hours is not None else [48, 24, 6]

    await tracked_collection.update_one(
        {"whatsapp_phone": phone, "opportunity_id": opp_id},
        {
            "$set": {
                "deadline_iso": deadline_iso,
                "deadline_timestamp": deadline_ts,
                "status": status,
                "reminder_hours": tiers,
                "reminded": False,
                "updated_at": time.time(),
            },
            "$setOnInsert": {
                "reminded_tiers": [],
                "tracked_at": time.time(),
            },
        },
        upsert=True,
    )
    # Tracking also acts as positive continuous learning signal
    await record_learning_feedback(phone, opp_id, "interested")
    return await tracked_collection.find_one({"whatsapp_phone": phone, "opportunity_id": opp_id})


async def record_learning_feedback(phone: str, opp_id: str, feedback: str) -> dict[str, Any]:
    opp = await opportunities_collection.find_one({"_id": opp_id})
    user = await users_collection.find_one({"whatsapp_phone": phone})
    if not opp or not user:
        return {}

    prefs = dict(user.get("learned_preferences") or {})
    liked = list(prefs.get("liked_keywords") or [])
    disliked = list(prefs.get("disliked_keywords") or [])
    keywords = _extract_keywords(opp)

    if feedback == "interested":
        for kw in keywords:
            if kw not in liked:
                liked.append(kw)
            if kw in disliked:
                disliked.remove(kw)
        await users_collection.update_one(
            {"whatsapp_phone": phone},
            {"$addToSet": {"interested_opportunities": opp_id}},
        )
    elif feedback == "not_interested":
        for kw in keywords:
            if kw not in disliked:
                disliked.append(kw)
            if kw in liked:
                liked.remove(kw)
        await users_collection.update_one(
            {"whatsapp_phone": phone},
            {"$addToSet": {"dismissed_opportunities": opp_id}},
        )

    prefs["liked_keywords"] = liked[-40:]
    prefs["disliked_keywords"] = disliked[-40:]
    prefs["updated_at"] = time.time()
    await users_collection.update_one(
        {"whatsapp_phone": phone},
        {"$set": {"learned_preferences": prefs}},
    )
    return prefs


async def _resolve_tracked_deadline_ts(item: dict[str, Any]) -> float | None:
    deadline_ts = item.get("deadline_timestamp")
    if deadline_ts is not None:
        return float(deadline_ts)
    dt = parse_deadline_dt(item.get("deadline_iso"))
    if not dt:
        return None
    ts = dt.timestamp()
    await tracked_collection.update_one({"_id": item["_id"]}, {"$set": {"deadline_timestamp": ts}})
    return ts


async def _process_tracked_reminder(item: dict[str, Any], now_ts: float) -> dict[str, Any] | None:
    status = str(item.get("status") or "interested").lower()
    if status in {"applied", "completed", "rejected"}:
        return None
    deadline_ts = await _resolve_tracked_deadline_ts(item)
    if deadline_ts is None:
        return None

    hours_left = (deadline_ts - now_ts) / 3600.0
    if hours_left <= 0:
        return None

    tiers = sorted([int(h) for h in (item.get("reminder_hours") or [48, 24, 6])], reverse=True)
    reminded_tiers = list(item.get("reminded_tiers") or [])
    if item.get("reminded") and "48h" not in reminded_tiers:
        reminded_tiers.append("48h")

    matches = [(t, f"{t}h") for t in tiers if hours_left <= t and f"{t}h" not in reminded_tiers]
    if not matches:
        return None
    tier_hours, tier_label = matches[-1]

    phone = item["whatsapp_phone"]
    opp = await opportunities_collection.find_one({"_id": item["opportunity_id"]})
    if not opp:
        return None

    user = await users_collection.find_one({"whatsapp_phone": phone})
    await send_deadline_reminder_card(
        to_phone=phone,
        opp=opp,
        hours_left=hours_left,
        tier_label=tier_label,
        preferred_language=(user or {}).get("preferred_language", "both"),
    )
    updated_tiers = list(dict.fromkeys(reminded_tiers + [f"{t}h" for t in tiers if t >= tier_hours]))
    await tracked_collection.update_one(
        {"_id": item["_id"]},
        {"$set": {"reminded": True, "reminded_tiers": updated_tiers, "last_reminded_at": now_ts}},
    )
    return {
        "whatsapp_phone": phone,
        "opportunity_id": opp["_id"],
        "title": opp.get("title"),
        "tier": tier_label,
        "hours_left": round(hours_left, 2),
    }


async def run_deadline_reminder_sweep(target_phone: str | None = None) -> list[dict[str, Any]]:
    """Checks all tracked opportunities with real datetime comparisons and sends tiered bilingual reminders."""
    now_ts = datetime.now(timezone.utc).timestamp()
    query: dict[str, Any] = {"whatsapp_phone": target_phone} if target_phone else {}
    items = await tracked_collection.find(query).to_list(length=500)
    sent_reminders: list[dict[str, Any]] = []
    for item in items:
        res = await _process_tracked_reminder(item, now_ts)
        if res:
            sent_reminders.append(res)
    return sent_reminders


async def send_daily_digest_for_user(phone: str) -> int:
    user = await users_collection.find_one({"whatsapp_phone": phone})
    if not user:
        return 0
    pref_lang = user.get("preferred_language", "both")

    candidates = (
        await opportunities_collection.find({"whatsapp_phone": phone, "digested": {"$ne": True}})
        .sort("priority_score", -1)
        .limit(5)
        .to_list(length=5)
    )
    if not candidates:
        return 0

    lines_en = ["*OppTrax Daily Opportunity Digest (Top Picks)*\n"]
    lines_hi = ["*ऑपट्रैक्स दैनिक अवसर डाइजेस्ट (आपके लिए टॉप अवसर)*\n"]

    for idx, opp in enumerate(candidates, 1):
        score_pct = int(round(float(opp.get("priority_score") or 0.5) * 100))
        dl = opp.get("deadline_iso") or "Not specified"
        lines_en.append(
            f"{idx}. *{opp.get('title')}* ({score_pct}% match)\n"
            f"   ID: `{opp.get('_id')}` | Deadline: {dl}\n"
            f"   {opp.get('url')}"
        )
        lines_hi.append(
            f"{idx}. *{opp.get('title_hi') or opp.get('title')}* ({score_pct}% मैच)\n"
            f"   ID: `{opp.get('_id')}` | अंतिम तिथि: {dl}\n"
            f"   {opp.get('url')}"
        )
        await opportunities_collection.update_one({"_id": opp["_id"]}, {"$set": {"digested": True}})

    lines_en.append("\nReply `details <ID>` for full details or `track <ID>` to get deadline reminders.")
    lines_hi.append("\nपूरी जानकारी के लिए `details <ID>` या रिमाइंडर के लिए `track <ID>` भेजें।")

    if pref_lang == "en":
        msg = "\n".join(lines_en)
    elif pref_lang == "hi":
        msg = "\n".join(lines_hi)
    else:
        msg = "\n".join(lines_en) + "\n\n━━━━━━━━━━━━━━━━━━━━\n\n" + "\n".join(lines_hi)

    await send_whatsapp_message(phone, msg)
    return len(candidates)
