import asyncio
import hashlib
import hmac
import html
import re
import time
from datetime import datetime, timezone
from typing import Any

import requests

from config import (
    GRAPH_API_VERSION,
    PHONE_NUMBER_ID,
    TWILIO_ACCOUNT_SID,
    TWILIO_AUTH_TOKEN,
    TWILIO_WHATSAPP_NUMBER,
    WHATSAPP_ALERT_TEMPLATE_BOTH,
    WHATSAPP_ALERT_TEMPLATE_EN,
    WHATSAPP_ALERT_TEMPLATE_HI,
    WHATSAPP_APP_ID,
    WHATSAPP_APP_SECRET,
    WHATSAPP_BOT_NUMBER,
    WHATSAPP_OTP_TEMPLATE_NAME,
    WHATSAPP_REMINDER_TEMPLATE_NAME,
    WHATSAPP_TEMPLATE_LANGUAGE,
    WHATSAPP_TOKEN,
    WHATSAPP_WABA_ID,
)
from database import whatsapp_messages_collection
from services.language import normalize_language


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    return text[: limit - 3] + "..."


def _format_deadline_display(deadline: str | None) -> str | None:
    if not deadline:
        return None
    raw = str(deadline).strip()
    if raw.lower() in {"null", "none", "n/a", ""}:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.strftime("%B %d, %Y (%H:%M UTC)")
    except ValueError:
        return raw


def verify_webhook_signature(raw_body: bytes, signature_header: str | None) -> bool:
    """Verifies Meta X-Hub-Signature-256 header when WHATSAPP_APP_SECRET is configured."""
    if not WHATSAPP_APP_SECRET:
        return True
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(
        WHATSAPP_APP_SECRET.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    provided = signature_header.split("=", 1)[1].strip()
    return hmac.compare_digest(expected, provided)


def get_whatsapp_connect_link(prefill_text: str = "Hi OppTrax! Start my opportunity copilot.") -> str | None:
    if not WHATSAPP_BOT_NUMBER:
        return None
    digits = "".join(c for c in WHATSAPP_BOT_NUMBER if c.isdigit())
    if not digits:
        return None
    encoded = requests.utils.quote(prefill_text)
    return f"https://wa.me/{digits}?text={encoded}"


def _extract_preview_text(payload: dict[str, Any]) -> str:
    msg_type = payload.get("type")
    if msg_type == "text":
        return payload.get("text", {}).get("body", "")
    if msg_type == "interactive":
        inter = payload.get("interactive", {})
        body = inter.get("body", {}).get("text", "")
        action = inter.get("action", {})
        if "buttons" in action:
            labels = [b.get("reply", {}).get("title", "") for b in action["buttons"]]
            return f"{body}\n[Buttons: {' | '.join(labels)}]"
        if "sections" in action:
            rows = []
            for sec in action["sections"]:
                for row in sec.get("rows", []):
                    rows.append(row.get("title", ""))
            return f"{body}\n[Menu: {', '.join(rows[:6])}]"
        return body
    if msg_type == "template":
        tpl = payload.get("template", {})
        return f"[Template: {tpl.get('name', 'template')}]"
    if msg_type == "audio":
        return "[Voice Audio Alert]"
    return str(payload)


def _extract_interactive_actions(payload: dict[str, Any]) -> list[dict[str, str]]:
    if payload.get("type") != "interactive":
        return []
    inter = payload.get("interactive", {})
    action = inter.get("action", {})
    buttons = []
    for btn in action.get("buttons", []):
        reply = btn.get("reply", {})
        if reply.get("id") and reply.get("title"):
            buttons.append({"id": reply["id"], "title": reply["title"], "kind": "button"})
    for sec in action.get("sections", []):
        for row in sec.get("rows", []):
            if row.get("id") and row.get("title"):
                buttons.append(
                    {
                        "id": row["id"],
                        "title": row["title"],
                        "description": row.get("description", ""),
                        "kind": "list_row",
                    }
                )
    return buttons


async def log_whatsapp_message(
    phone: str,
    direction: str,
    msg_type: str,
    text: str,
    delivery_status: str,
    payload: dict[str, Any] | None = None,
    actions: list[dict[str, str]] | None = None,
    error_detail: str | None = None,
) -> None:
    try:
        await whatsapp_messages_collection.insert_one(
            {
                "whatsapp_phone": phone,
                "direction": direction,
                "type": msg_type,
                "text": text,
                "actions": actions or [],
                "delivery_status": delivery_status,
                "error_detail": error_detail,
                "payload": payload or {},
                "created_at": time.time(),
                "created_at_iso": datetime.now(timezone.utc).isoformat(),
            }
        )
    except Exception as log_err:
        print(f"[WHATSAPP LOG] Failed to persist message log: {log_err}", flush=True)


def _send_meta_sync(data: dict[str, Any]) -> tuple[bool, str, str | None]:
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }
    res = requests.post(url, headers=headers, json=data, timeout=15)
    if res.status_code >= 400:
        err_text = res.text
        try:
            res_json = res.json()
            error_msg = res_json.get("error", {}).get("message", "Unknown Meta API error")
            error_code = res_json.get("error", {}).get("code", res.status_code)
            formatted = f"Meta API Error {error_code}: {error_msg}"
        except ValueError:
            formatted = f"Meta API Error Status {res.status_code}: {err_text}"
        return False, "failed_meta", formatted
    return True, "sent_meta", None


def _send_twilio_sync(to_phone: str, body_text: str) -> tuple[bool, str, str | None]:
    url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Messages.json"
    from_num = TWILIO_WHATSAPP_NUMBER
    if not from_num.startswith("whatsapp:"):
        from_num = f"whatsapp:+{from_num.lstrip('+')}"
    to_num = f"whatsapp:+{to_phone.lstrip('+')}"
    res = requests.post(
        url,
        auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
        data={"From": from_num, "To": to_num, "Body": body_text[:1590]},
        timeout=15,
    )
    if res.status_code >= 400:
        return False, "failed_twilio", f"Twilio API Error {res.status_code}: {res.text}"
    return True, "sent_twilio", None


async def _deliver_and_log(
    send_fn,
    data: dict[str, Any],
    to_phone: str,
    msg_type: str,
    preview: str,
    actions: list[dict[str, str]],
    raise_on_error: bool,
) -> dict[str, Any]:
    try:
        ok, status_label, err_msg = await asyncio.to_thread(send_fn)
    except requests.RequestException as exc:
        err_msg = f"WhatsApp Network Error: {exc}"
        print(f"[WHATSAPP] {err_msg}", flush=True)
        await log_whatsapp_message(to_phone, "outbound", msg_type, preview, "failed_network", data, actions, err_msg)
        if raise_on_error:
            raise RuntimeError(err_msg) from exc
        return {"ok": False, "status": "failed_network", "error": err_msg}

    await log_whatsapp_message(to_phone, "outbound", msg_type, preview, status_label, data, actions, err_msg)
    if not ok:
        print(f"[WHATSAPP] {err_msg}", flush=True)
        if raise_on_error:
            raise RuntimeError(err_msg)
    return {"ok": ok, "status": status_label, "error": err_msg}


async def _post_whatsapp(data: dict[str, Any], raise_on_error: bool = False) -> dict[str, Any]:
    to_phone = str(data.get("to") or "")
    msg_type = str(data.get("type") or "text")
    preview = _extract_preview_text(data)
    actions = _extract_interactive_actions(data)

    if WHATSAPP_TOKEN and PHONE_NUMBER_ID:
        return await _deliver_and_log(
            lambda: _send_meta_sync(data), data, to_phone, msg_type, preview, actions, raise_on_error
        )

    if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_WHATSAPP_NUMBER:
        return await _deliver_and_log(
            lambda: _send_twilio_sync(to_phone, preview), data, to_phone, msg_type, preview, actions, raise_on_error
        )

    await log_whatsapp_message(to_phone, "outbound", msg_type, preview, "sandbox_queued", data, actions)
    print(f"[WHATSAPP SANDBOX] -> {to_phone}: {preview[:120]}...", flush=True)
    return {"ok": True, "status": "sandbox_queued", "error": None}


def format_html_for_whatsapp(raw_html: str) -> str:
    if not raw_html:
        return ""
    text = html.unescape(raw_html)
    text = re.sub(r"<h[1-6]>(.*?)</h[1-6]>", r"*\1*\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<p>(.*?)</p>", r"\1\n", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<b>(.*?)</b>", r"*\1*", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<strong>(.*?)</strong>", r"*\1*", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<i>(.*?)</i>", r"_\1_", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<em>(.*?)</em>", r"_\1_", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<li>(.*?)</li>", r"• \1\n", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<br\s*/?>", r"\n", text, flags=re.IGNORECASE)
    text = re.sub(
        r'<a\s+[^>]*href=["\'](.*?)["\'][^>]*>(.*?)</a>',
        r"\2 (\1)",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _chunk_text(text_content: str, chunk_size: int = 3900) -> list[str]:
    if len(text_content) <= chunk_size:
        return [text_content]
    chunks: list[str] = []
    remaining = text_content
    while len(remaining) > chunk_size:
        split_at = remaining.rfind("\n", 0, chunk_size)
        if split_at < chunk_size // 2:
            split_at = chunk_size
        chunks.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()
    if remaining:
        chunks.append(remaining)
    return chunks


async def send_whatsapp_message(
    to_phone: str,
    text_content: str,
    raise_on_error: bool = False,
) -> list[dict[str, Any]]:
    results = []
    for chunk in _chunk_text(text_content, 3900):
        res = await _post_whatsapp(
            {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to_phone,
                "type": "text",
                "text": {"preview_url": True, "body": chunk},
            },
            raise_on_error=raise_on_error,
        )
        results.append(res)
    return results


async def send_otp_message(to_phone: str, otp_code: str) -> dict[str, Any]:
    """Sends OTP verification code via Meta template (if configured) or text fallback."""
    if WHATSAPP_OTP_TEMPLATE_NAME:
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to_phone,
            "type": "template",
            "template": {
                "name": WHATSAPP_OTP_TEMPLATE_NAME,
                "language": {"code": WHATSAPP_TEMPLATE_LANGUAGE},
                "components": [
                    {"type": "body", "parameters": [{"type": "text", "text": otp_code}]},
                    {
                        "type": "button",
                        "sub_type": "url",
                        "index": "0",
                        "parameters": [{"type": "text", "text": otp_code}],
                    },
                ],
            },
        }
        try:
            return await _post_whatsapp(payload, raise_on_error=True)
        except RuntimeError as exc:
            if "132000" not in str(exc) and "button" not in str(exc).lower():
                raise
            payload["template"] = {
                "name": WHATSAPP_OTP_TEMPLATE_NAME,
                "language": {"code": WHATSAPP_TEMPLATE_LANGUAGE},
                "components": [{"type": "body", "parameters": [{"type": "text", "text": otp_code}]}],
            }
            return await _post_whatsapp(payload, raise_on_error=True)

    msg_text = (
        f"*OppTrax Verification Code:* *{otp_code}*\n"
        f"Valid for 5 minutes. Enter this code on the OppTrax website to connect WhatsApp.\n\n"
        f"*ऑपट्रैक्स सत्यापन कोड:* *{otp_code}*\n"
        f"यह कोड 5 मिनट के लिए मान्य है।"
    )
    results = await send_whatsapp_message(to_phone, msg_text, raise_on_error=True)
    return results[0] if results else {"ok": True, "status": "sent"}


async def send_language_selector(to_phone: str) -> dict[str, Any]:
    body = (
        "*Choose Your Preferred Language / अपनी पसंदीदा भाषा चुनें*\n\n"
        "• *Both (EN + हिंदी)*: Opportunity alerts in both Hindi & English\n"
        "• *English*: All alerts & Q&A in English\n"
        "• *हिंदी (Hindi)*: सभी जानकारी और सवाल-जवाब हिंदी में"
    )
    return await _post_whatsapp(
        {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to_phone,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": _truncate(body, 1024)},
                "action": {
                    "buttons": [
                        {"type": "reply", "reply": {"id": "lang_both", "title": _truncate("Both EN + हिंदी", 20)}},
                        {"type": "reply", "reply": {"id": "lang_en", "title": _truncate("English", 20)}},
                        {"type": "reply", "reply": {"id": "lang_hi", "title": _truncate("हिंदी (Hindi)", 20)}},
                    ]
                },
            },
        }
    )


async def send_bilingual_welcome_and_onboarding(to_phone: str) -> dict[str, Any]:
    welcome_text = (
        "*Welcome to OppTrax AI Opportunity Copilot!*\n"
        "*ऑपट्रैक्स AI अवसर कोपायलट में आपका स्वागत है!*\n\n"
        "I use *Yutori* to discover personalized internships, jobs, hackathons & scholarships for you, "
        "inform you in *English + हिंदी*, remind you before deadlines, and answer your questions.\n\n"
        "मैं आपके लिए *Yutori* से सही अवसर खोजकर *हिंदी और English* दोनों में भेजूंगा, "
        "डेडलाइन से पहले याद दिलाऊंगा और आपके सवालों का जवाब दूंगा।\n\n"
        "*Quick Commands:* `search <query>`, `saved`, `list`, `lang en|hi|both`, `help`"
    )
    return await _post_whatsapp(
        {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to_phone,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": _truncate(welcome_text, 1024)},
                "action": {
                    "buttons": [
                        {"type": "reply", "reply": {"id": "onboard_upload", "title": "Upload Resume"}},
                        {"type": "reply", "reply": {"id": "onboard_choices", "title": "Pick Tracks"}},
                        {"type": "reply", "reply": {"id": "onboard_skip", "title": "Start Searching"}},
                    ]
                },
            },
        }
    )


async def send_scout_list_menu(to_phone: str, active_tasks: list[dict[str, Any]]) -> dict[str, Any]:
    rows = []
    for task in active_tasks[:10]:
        title = _truncate(str(task.get("query_instruction", "Scout")), 24)
        tid = str(task.get("yutori_task_id", ""))
        finding_count = len(task.get("findings", []))
        rows.append(
            {
                "id": f"select_{tid}",
                "title": title,
                "description": _truncate(f"{finding_count} findings | {tid[:10]}", 72),
            }
        )

    return await _post_whatsapp(
        {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to_phone,
            "type": "interactive",
            "interactive": {
                "type": "list",
                "header": {"type": "text", "text": "OppTrax Control Panel"},
                "body": {
                    "text": "Select an active Yutori scout below to inspect findings or stop it.\n"
                    "नीचे किसी सक्रिय स्काउट को चुनें।"
                },
                "footer": {"text": _truncate(f"{len(active_tasks)} active scout(s)", 60)},
                "action": {
                    "button": "View Scouts",
                    "sections": [{"title": "Active Scouts", "rows": rows}],
                },
            },
        }
    )


async def send_action_buttons(
    to_phone: str,
    task_id: str,
    instruction: str,
    finding_count: int,
    is_active: bool = True,
) -> dict[str, Any]:
    buttons = [
        {"type": "reply", "reply": {"id": f"status_{task_id}", "title": "View Findings"}}
    ]
    if is_active:
        buttons.append({"type": "reply", "reply": {"id": f"stop_{task_id}", "title": "Stop Scout"}})

    return await _post_whatsapp(
        {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to_phone,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {
                    "text": _truncate(
                        f"*Scout Detail / स्काउट विवरण*\n\n"
                        f"Query: _{instruction}_\n"
                        f"Findings collected: {finding_count}\n\n"
                        f"Choose an action:",
                        1024,
                    )
                },
                "action": {"buttons": buttons},
            },
        }
    )


def build_opportunity_full_text(opp: dict[str, Any], preferred_language: str = "both") -> str:
    lang = normalize_language(preferred_language, default="both")
    title = opp.get("title", "Opportunity")
    title_hi = opp.get("title_hi") or title
    org = opp.get("organization") or "Not specified"
    category = opp.get("category") or opp.get("task_type") or "Opportunity"
    location = opp.get("location") or "Not specified"
    stipend = opp.get("stipend_or_reward") or "Not specified"
    eligibility = opp.get("eligibility") or "Check official link"
    steps = opp.get("application_steps") or "Apply via official link below"
    deadline_disp = _format_deadline_display(opp.get("deadline_iso")) or "Not specified"
    url = opp.get("url") or ""
    score = opp.get("priority_score")
    reasoning = opp.get("reasoning")
    summary_en = opp.get("full_details_en") or opp.get("summary_en") or opp.get("summary") or ""
    summary_hi = opp.get("full_details_hi") or opp.get("summary_hi") or ""
    finding_id = opp.get("_id") or opp.get("finding_id") or ""

    match_line_en = f"*Match Score:* {int(round(float(score) * 100))}%\n" if score is not None else ""
    reason_line_en = f"*Why it matches:* {reasoning}\n" if reasoning else ""

    en_block = (
        f"*[English] {title}*\n"
        f"*ID:* `{finding_id}` | *Type:* {category}\n"
        f"*Organization:* {org}\n"
        f"*Location:* {location}\n"
        f"*Stipend / Reward:* {stipend}\n"
        f"*Eligibility:* {eligibility}\n"
        f"*Deadline:* {deadline_disp}\n"
        f"{match_line_en}{reason_line_en}\n"
        f"*Details:*\n{summary_en}\n\n"
        f"*How to Apply:* {steps}\n"
        f"*Official Link:* {url}"
    )

    hi_block = (
        f"*[हिंदी] {title_hi}*\n"
        f"*संस्था:* {org} | *स्थान:* {location}\n"
        f"*स्टाइपेंड / पुरस्कार:* {stipend}\n"
        f"*पात्रता:* {eligibility}\n"
        f"*अंतिम तिथि:* {deadline_disp}\n\n"
        f"*पूरी जानकारी:*\n{summary_hi or summary_en}\n\n"
        f"*आवेदन लिंक:* {url}"
    )

    if lang == "en":
        return en_block
    if lang == "hi":
        return hi_block
    return f"{en_block}\n\n━━━━━━━━━━━━━━━━━━━━\n\n{hi_block}"


def _should_try_template_fallback(result: dict[str, Any]) -> bool:
    return not result.get("ok") and "131047" in str(result.get("error") or "")


def _select_alert_template(lang: str) -> tuple[str | None, str]:
    mapping = {
        "hi": (WHATSAPP_ALERT_TEMPLATE_HI, "hi"),
        "en": (WHATSAPP_ALERT_TEMPLATE_EN, "en_US"),
        "both": (WHATSAPP_ALERT_TEMPLATE_BOTH, "en_US"),
    }
    return mapping.get(lang, (WHATSAPP_ALERT_TEMPLATE_BOTH, "en_US"))


async def _send_three_param_template(
    to_phone: str,
    template_name: str,
    locale: str,
    param1: str,
    param2: str,
    param3: str,
) -> dict[str, Any]:
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to_phone,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": locale},
            "components": [
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": _truncate(param1, 150)},
                        {"type": "text", "text": _truncate(param2, 350)},
                        {"type": "text", "text": _truncate(param3, 300) or "https://opptrax.local"},
                    ],
                }
            ],
        },
    }
    return await _post_whatsapp(payload)


def _fetch_meta_setup_sync() -> dict[str, Any]:
    if not (WHATSAPP_TOKEN and PHONE_NUMBER_ID):
        return {"configured": False}
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}
    result: dict[str, Any] = {
        "configured": True,
        "graph_api_version": GRAPH_API_VERSION,
        "phone_number_id": PHONE_NUMBER_ID,
        "app_id": WHATSAPP_APP_ID,
        "waba_id": WHATSAPP_WABA_ID,
    }
    try:
        res = requests.get(
            f"https://graph.facebook.com/{GRAPH_API_VERSION}/{PHONE_NUMBER_ID}",
            params={"fields": "display_phone_number,verified_name,quality_rating"},
            headers=headers,
            timeout=10,
        )
        result["phone_probe_status"] = res.status_code
        if res.status_code == 200:
            data = res.json()
            result["display_phone_number"] = data.get("display_phone_number")
            result["verified_name"] = data.get("verified_name")
            result["quality_rating"] = data.get("quality_rating")
        else:
            result["phone_probe_error"] = res.json().get("error", {}).get("message", res.text[:200])
    except Exception as exc:
        result["phone_probe_error"] = str(exc)

    if WHATSAPP_WABA_ID:
        try:
            waba_res = requests.get(
                f"https://graph.facebook.com/{GRAPH_API_VERSION}/{WHATSAPP_WABA_ID}",
                params={"fields": "id,name"},
                headers=headers,
                timeout=10,
            )
            result["waba_probe_status"] = waba_res.status_code
            if waba_res.status_code == 200:
                result["waba_name"] = waba_res.json().get("name")
        except Exception as exc:
            result["waba_probe_error"] = str(exc)
    return result


async def fetch_meta_setup_diagnostics() -> dict[str, Any]:
    """Read-only verification of Meta Cloud API Phone Number & WABA identifiers."""
    return await asyncio.to_thread(_fetch_meta_setup_sync)


async def send_opportunity_card(
    to_phone: str,
    finding_id: str,
    title: str,
    summary: str,
    url: str,
    task_type: str = "CAREER",
    priority_score: float | None = None,
    reasoning: str | None = None,
    deadline: str | None = None,
    summary_hi: str | None = None,
    title_hi: str | None = None,
    organization: str | None = None,
    location: str | None = None,
    stipend_or_reward: str | None = None,
    eligibility: str | None = None,
    application_steps: str | None = None,
    full_details_en: str | None = None,
    full_details_hi: str | None = None,
    preferred_language: str = "both",
    send_full_brief: bool = True,
) -> dict[str, Any]:
    """Delivers complete bilingual opportunity details plus an interactive WhatsApp action card."""
    lang = normalize_language(preferred_language, default="both")
    opp_payload = {
        "_id": finding_id,
        "title": title,
        "title_hi": title_hi or title,
        "summary": summary,
        "summary_en": summary,
        "summary_hi": summary_hi,
        "full_details_en": full_details_en or summary,
        "full_details_hi": full_details_hi or summary_hi,
        "organization": organization,
        "category": task_type,
        "location": location,
        "stipend_or_reward": stipend_or_reward,
        "eligibility": eligibility,
        "application_steps": application_steps,
        "deadline_iso": deadline,
        "url": url,
        "priority_score": priority_score,
        "reasoning": reasoning,
    }

    # 1. Send full untruncated bilingual details message first (supports up to 4096 chars/chunk)
    if send_full_brief:
        full_brief = build_opportunity_full_text(opp_payload, preferred_language=lang)
        await send_whatsapp_message(to_phone, full_brief)

    # 2. Send compact interactive button card (strictly <=1024 chars, <=3 buttons, <=20-char labels)
    deadline_disp = _format_deadline_display(deadline)
    score_str = f"Match: {int(round(float(priority_score) * 100))}% | " if priority_score is not None else ""
    header_title = title if lang != "hi" else (title_hi or title)
    card_header = f"*OppTrax Match Alert*\n*{_truncate(header_title, 120)}*\n"
    if score_str or deadline_disp:
        card_header += f"{score_str}" + (f"Deadline: {deadline_disp}" if deadline_disp else "") + "\n"

    card_footer = (
        f"\n*Link:* {url}\n"
        f"_Reply `interested`, `skip`, or `search {finding_id}` anytime._"
    )

    if lang == "both":
        compact_en = _truncate(summary, 280)
        compact_hi = _truncate(summary_hi or summary, 280)
        card_body_mid = f"\n*EN:* {compact_en}\n*HI:* {compact_hi}"
    elif lang == "hi":
        card_body_mid = f"\n{_truncate(summary_hi or summary, 580)}"
    else:
        card_body_mid = f"\n{_truncate(summary, 580)}"

    available = max(80, 1024 - len(card_header) - len(card_footer) - 4)
    card_body = f"{card_header}{_truncate(card_body_mid, available)}{card_footer}"

    buttons = [
        {"type": "reply", "reply": {"id": f"track_{finding_id}", "title": "Save / Track"}},
        {"type": "reply", "reply": {"id": f"details_{finding_id}", "title": "Full Details"}},
        {"type": "reply", "reply": {"id": f"ask_{finding_id}", "title": "Ask AI"}},
    ]

    result = await _post_whatsapp(
        {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to_phone,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": _truncate(card_body, 1024)},
                "action": {"buttons": buttons},
            },
        }
    )
    if _should_try_template_fallback(result):
        tpl_name, tpl_lang = _select_alert_template(lang)
        if tpl_name:
            short_note = summary_hi or summary if lang == "hi" else summary
            return await _send_three_param_template(to_phone, tpl_name, tpl_lang, header_title, short_note, url)
    return result


async def send_full_opportunity_details(
    to_phone: str,
    opp: dict[str, Any],
    preferred_language: str = "both",
) -> dict[str, Any]:
    finding_id = str(opp.get("_id", ""))
    full_msg = build_opportunity_full_text(opp, preferred_language=preferred_language)
    await send_whatsapp_message(to_phone, full_msg)

    action_prompt = (
        f"*Actions for:* {_truncate(str(opp.get('title', 'Opportunity')), 120)}\n"
        f"Track it for deadline reminders, ask questions in Hindi/English, or run deep search."
    )
    return await _post_whatsapp(
        {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to_phone,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": _truncate(action_prompt, 1024)},
                "action": {
                    "buttons": [
                        {"type": "reply", "reply": {"id": f"track_{finding_id}", "title": "Save / Track"}},
                        {"type": "reply", "reply": {"id": f"research_{finding_id}", "title": "Deep Search"}},
                        {"type": "reply", "reply": {"id": f"ask_{finding_id}", "title": "Ask AI"}},
                    ]
                },
            },
        }
    )


async def send_deadline_reminder_card(
    to_phone: str,
    opp: dict[str, Any],
    hours_left: float,
    tier_label: str = "48h",
    preferred_language: str = "both",
) -> dict[str, Any]:
    lang = normalize_language(preferred_language, default="both")
    finding_id = str(opp.get("_id", ""))
    title = opp.get("title", "Tracked Opportunity")
    title_hi = opp.get("title_hi") or title
    deadline_disp = _format_deadline_display(opp.get("deadline_iso")) or "Closing soon"
    url = opp.get("url") or ""
    hrs = max(1, int(round(hours_left)))

    en_part = (
        f"*[Deadline Reminder - {tier_label}]*\n"
        f"*{_truncate(title, 150)}* closes in ~*{hrs} hours* ({deadline_disp}).\n"
        f"Apply now: {url}"
    )
    hi_part = (
        f"*[अंतिम तिथि रिमाइंडर - {tier_label}]*\n"
        f"*{_truncate(title_hi, 150)}* की अंतिम तिथि लगभग *{hrs} घंटे* में समाप्त हो रही है ({deadline_disp})।\n"
        f"आवेदन लिंक: {url}"
    )

    if lang == "en":
        body = en_part
    elif lang == "hi":
        body = hi_part
    else:
        body = f"{en_part}\n\n{hi_part}"

    result = await _post_whatsapp(
        {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to_phone,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": _truncate(body, 1024)},
                "action": {
                    "buttons": [
                        {"type": "reply", "reply": {"id": f"applied_{finding_id}", "title": "Mark Applied"}},
                        {"type": "reply", "reply": {"id": f"details_{finding_id}", "title": "Full Details"}},
                        {"type": "reply", "reply": {"id": f"ask_{finding_id}", "title": "Ask AI"}},
                    ]
                },
            },
        }
    )
    if _should_try_template_fallback(result) and WHATSAPP_REMINDER_TEMPLATE_NAME:
        tpl_title = title_hi if lang != "en" else title
        return await _send_three_param_template(
            to_phone,
            WHATSAPP_REMINDER_TEMPLATE_NAME,
            "hi",
            tpl_title,
            deadline_disp,
            url,
        )
    return result


async def send_onboarding_choices(to_phone: str) -> dict[str, Any]:
    """Sends a rich interactive list asking the user what to monitor."""
    rows = [
        {"id": "onboard_internships", "title": "Internships", "description": "Track internships matching my skills"},
        {"id": "onboard_jobs", "title": "Full-Time Roles", "description": "Monitor entry/mid level jobs"},
        {"id": "onboard_hackathons", "title": "Hackathons", "description": "Find open source & hackathons"},
        {"id": "onboard_conferences", "title": "Conferences", "description": "Find tech conferences & summits"},
        {"id": "onboard_scholarships", "title": "Scholarships", "description": "Track international tech scholarships"},
        {"id": "onboard_flights", "title": "BLR-DEL Flights", "description": "Track flight prices under 5000 INR"},
        {"id": "onboard_vc_sf", "title": "SF VC Seed Rounds", "description": "Track SF funding announcements"},
        {"id": "onboard_esoc", "title": "Euro Summer of Code", "description": "Track ESoC project additions"},
        {"id": "onboard_blr_events", "title": "Bengaluru Events", "description": "Track free tech events in BLR"},
        {"id": "onboard_manual", "title": "Manual Mode Only", "description": "I will specify what to track manually"},
    ]
    return await _post_whatsapp(
        {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to_phone,
            "type": "interactive",
            "interactive": {
                "type": "list",
                "header": {"type": "text", "text": "Select Your Tracking Mode"},
                "body": {
                    "text": "I can automatically deploy a LiveScout monitor based on your profile.\n"
                    "आप किस तरह के अवसर ट्रैक करना चाहते हैं?"
                },
                "footer": {"text": "You can change this anytime."},
                "action": {
                    "button": "Choose an Option",
                    "sections": [{"title": "Autonomous Deployment", "rows": rows}],
                },
            },
        }
    )


def _upload_media_sync(file_path: str) -> str | None:
    if not WHATSAPP_TOKEN or not PHONE_NUMBER_ID:
        return None
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{PHONE_NUMBER_ID}/media"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}
    with open(file_path, "rb") as f:
        files = {
            "file": ("alert.mp3", f, "audio/mpeg"),
            "type": (None, "audio"),
            "messaging_product": (None, "whatsapp"),
        }
        response = requests.post(url, headers=headers, files=files, timeout=15)
    if response.status_code == 200:
        return response.json().get("id")
    print(f"[UPLOAD] Meta media upload failed ({response.status_code}): {response.text}", flush=True)
    return None


async def upload_whatsapp_media(file_path: str) -> str | None:
    print(f"[UPLOAD] Sending {file_path} to Meta servers...", flush=True)
    try:
        return await asyncio.to_thread(_upload_media_sync, file_path)
    except Exception as exc:
        print(f"[UPLOAD] Request failed: {exc}", flush=True)
        return None


async def send_whatsapp_audio(to_phone: str, media_id: str) -> dict[str, Any]:
    return await _post_whatsapp(
        {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to_phone,
            "type": "audio",
            "audio": {"id": media_id},
        }
    )
