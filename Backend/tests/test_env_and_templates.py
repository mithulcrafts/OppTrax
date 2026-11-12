import asyncio
import hashlib
import hmac
import json

import config
from database import opportunities_collection, tasks_collection, users_collection
from services import ai_engine, live_scout, yutori_client
from services.whatsapp_api import get_whatsapp_connect_link, send_deadline_reminder_card, send_opportunity_card
from tasks.cron_jobs import poll_single_yutori_task


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


def test_yutori_client_contracts_and_webhook_url_gating(monkeypatch):
    captured_posts = []

    def fake_post(url, json=None, headers=None, timeout=15):
        captured_posts.append({"url": url, "json": json, "headers": headers})
        return _FakeResponse(201, {"id": "yt_task_abc", "status": "queued"})

    def fake_get(url, headers=None, timeout=15):
        return _FakeResponse(200, {"updates": [{"content": "New fellowship found"}]})

    monkeypatch.setattr(yutori_client, "ENABLE_YUTORI", True)
    monkeypatch.setattr(yutori_client, "SCOUT_PROVIDER", "yutori")
    monkeypatch.setattr(yutori_client, "YUTORI_KEY", "test_yt_key")
    monkeypatch.setattr(yutori_client.requests, "post", fake_post)
    monkeypatch.setattr(yutori_client.requests, "get", fake_get)

    # Blank YUTORI_WEBHOOK_BASE_URL must NOT attach webhook_url even if NGROK_BASE_URL exists
    monkeypatch.setattr(yutori_client, "get_yutori_webhook_base_url", lambda: None)
    scout_id = asyncio.run(yutori_client.launch_yutori_scout("Find Python fellowships"))
    assert scout_id == "yt_task_abc"
    assert "webhook_url" not in captured_posts[-1]["json"]

    research_id = asyncio.run(yutori_client.launch_yutori_research("Research Sarvam Fellowship"))
    assert research_id == "yt_task_abc"
    assert "webhook_url" not in captured_posts[-1]["json"]

    # Explicit YUTORI_WEBHOOK_BASE_URL attaches /yutori-webhook
    monkeypatch.setattr(yutori_client, "get_yutori_webhook_base_url", lambda: "https://example.ngrok.dev")
    research_id_2 = asyncio.run(yutori_client.launch_yutori_research("Research GSOC"))
    assert research_id_2 == "yt_task_abc"
    assert captured_posts[-1]["json"]["webhook_url"] == "https://example.ngrok.dev/yutori-webhook"

    updates_payload = asyncio.run(yutori_client.fetch_yutori_updates("yt_task_abc"))
    assert isinstance(updates_payload, dict)
    assert isinstance(updates_payload.get("updates"), list)
    assert updates_payload["updates"][0]["content"] == "New fellowship found"


def test_yutori_webhook_secret_enforcement(client, monkeypatch):
    monkeypatch.setattr("routers.yutori_webhook.YUTORI_WEBHOOK_SECRET", "top_secret_yt")
    body = json.dumps({"task_id": "missing_task", "updates": []}).encode("utf-8")

    no_secret = client.post("/yutori-webhook", content=body, headers={"Content-Type": "application/json"})
    assert no_secret.status_code == 403

    bad_secret = client.post(
        "/yutori-webhook",
        content=body,
        headers={"Content-Type": "application/json", "X-Webhook-Secret": "wrong"},
    )
    assert bad_secret.status_code == 403

    good_hmac = "sha256=" + hmac.new(b"top_secret_yt", body, hashlib.sha256).hexdigest()
    hmac_ok = client.post(
        "/yutori-webhook",
        content=body,
        headers={"Content-Type": "application/json", "X-Yutori-Signature": good_hmac},
    )
    assert hmac_ok.status_code == 200
    assert hmac_ok.json()["reason"] == "task_not_found"

    bearer_ok = client.post(
        "/yutori-webhook",
        content=body,
        headers={"Content-Type": "application/json", "Authorization": "Bearer top_secret_yt"},
    )
    assert bearer_ok.status_code == 200


def test_whatsapp_business_link_and_config_diagnostics(client, monkeypatch):
    monkeypatch.setattr("services.whatsapp_api.WHATSAPP_BOT_NUMBER", "15556679357")
    link = get_whatsapp_connect_link("Hello")
    assert link == "https://wa.me/15556679357?text=Hello"

    res = client.get("/api/auth/whatsapp-config")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert "graph_api_version" in data["providers"]
    assert "whatsapp_templates" in data["providers"]
    assert "database_backend" in data["providers"]


def test_whatsapp_131047_template_fallback(monkeypatch):
    sent_payloads = []

    def fake_send_meta(payload):
        sent_payloads.append(payload)
        if payload.get("type") == "interactive":
            return False, "failed_meta", "Meta API Error 131047: Re-engagement message"
        return True, "sent_meta", None

    monkeypatch.setattr("services.whatsapp_api.WHATSAPP_TOKEN", "fake_token")
    monkeypatch.setattr("services.whatsapp_api.PHONE_NUMBER_ID", "1117898394750720")
    monkeypatch.setattr("services.whatsapp_api.WHATSAPP_ALERT_TEMPLATE_HI", "avsar_alert_hi")
    monkeypatch.setattr("services.whatsapp_api.WHATSAPP_REMINDER_TEMPLATE_NAME", "avsar_reminder_hi")
    monkeypatch.setattr("services.whatsapp_api._send_meta_sync", fake_send_meta)

    alert_res = asyncio.run(
        send_opportunity_card(
            to_phone="919876543210",
            finding_id="opp_1",
            title="AI Research Grant",
            title_hi="AI रिसर्च ग्रांट",
            summary="Research grant summary",
            summary_hi="रिसर्च ग्रांट सारांश",
            url="https://example.org/grant",
            preferred_language="hi",
            send_full_brief=False,
        )
    )
    assert alert_res["ok"] is True
    assert sent_payloads[-1]["type"] == "template"
    assert sent_payloads[-1]["template"]["name"] == "avsar_alert_hi"
    assert sent_payloads[-1]["template"]["language"]["code"] == "hi"

    rem_res = asyncio.run(
        send_deadline_reminder_card(
            to_phone="919876543210",
            opp={
                "_id": "opp_1",
                "title": "AI Research Grant",
                "title_hi": "AI रिसर्च ग्रांट",
                "deadline_iso": "2026-12-30T18:30:00Z",
                "url": "https://example.org/grant",
            },
            hours_left=12,
            tier_label="12h",
            preferred_language="hi",
        )
    )
    assert rem_res["ok"] is True
    assert sent_payloads[-1]["type"] == "template"
    assert sent_payloads[-1]["template"]["name"] == "avsar_reminder_hi"
    assert sent_payloads[-1]["template"]["language"]["code"] == "hi"


def test_sqlite_database_backend_loaded():
    assert config.DATABASE_BACKEND in {"sqlite", "auto", "mongodb"}
    assert config.GRAPH_API_VERSION.startswith("v")
    assert config.is_retryable_error(RuntimeError("401 UNAUTHENTICATED: API key not valid")) is False


def test_qa_graceful_bilingual_fallback_on_gemini_auth_error(monkeypatch):
    calls = {"count": 0}

    async def fail_auth(*args, **kwargs):
        calls["count"] += 1
        raise RuntimeError("401 UNAUTHENTICATED: API key not valid")

    monkeypatch.setattr(ai_engine, "gemini_client", object())
    monkeypatch.setattr(ai_engine, "generate_content_with_retry", fail_auth)

    opp = {
        "title": "AI Grant",
        "title_hi": "AI अनुदान",
        "organization": "OppTrax Foundation",
        "stipend_or_reward": "INR 50,000",
        "eligibility": "Students",
        "deadline_iso": "2026-12-30",
        "summary_hi": "छात्रों के लिए रिसर्च अनुदान।",
        "url": "https://example.org/grant",
    }
    res = asyncio.run(ai_engine.answer_opportunity_question(opp=opp, question="पात्रता क्या है?", preferred_language="hi"))
    assert calls["count"] == 1
    assert res["language"] == "hi"
    assert "AI अनुदान" in res["answer"]

    draft = asyncio.run(
        ai_engine.generate_application_draft(opp=opp, user_profile={"skills": ["Python"]}, preferred_language="en")
    )
    assert "Application Strategy for AI Grant" in draft


def test_livescout_default_mode_and_sarvam_qa_fallback(monkeypatch):
    monkeypatch.setattr(yutori_client, "ENABLE_YUTORI", False)
    monkeypatch.setattr(yutori_client, "SCOUT_PROVIDER", "livescout")

    scout_id = asyncio.run(yutori_client.launch_yutori_scout("Find Python hackathons"))
    research_id = asyncio.run(yutori_client.launch_yutori_research("Research AI Grant"))
    updates = asyncio.run(yutori_client.fetch_yutori_updates(scout_id))

    assert scout_id.startswith("livescout_")
    assert research_id.startswith("livescout_res_")
    assert updates == {"updates": []}

    async def fake_sarvam_chat(prompt: str, max_tokens: int = 900) -> str | None:
        return "यह छात्रवृत्ति बी.टेक और एम.टेक छात्रों के लिए खुली है और ₹50,000 प्रदान करती है।"

    def fail_gemini(*args, **kwargs):
        raise RuntimeError("401 UNAUTHENTICATED")

    monkeypatch.setattr(ai_engine, "gemini_client", object())
    monkeypatch.setattr(ai_engine, "generate_content_with_retry", fail_gemini)
    monkeypatch.setattr(ai_engine, "generate_sarvam_chat", fake_sarvam_chat)

    opp = {
        "title": "National AI Scholarship",
        "organization": "OppTrax Foundation",
        "stipend_or_reward": "INR 50,000",
        "eligibility": "B.Tech / M.Tech students",
        "url": "https://example.org/ai-scholarship",
    }
    ans = asyncio.run(
        ai_engine.answer_opportunity_question(opp=opp, question="क्या मैं पात्र हूँ?", preferred_language="hi")
    )
    assert ans["language"] == "hi"
    assert "₹50,000" in ans["answer"]


def test_livescout_search_hindi_dedup_ranking_and_translation(monkeypatch):
    monkeypatch.setattr(live_scout, "LIVE_SCOUT_NETWORK_ENABLED", True)

    async def fake_translate_hi(text: str) -> str | None:
        if not text:
            return None
        return f"[HI] {text[:60]}"

    monkeypatch.setattr(live_scout, "translate_to_hindi_sarvam", fake_translate_hi)

    def fake_get(url: str, headers=None, timeout=10):
        if "unstop.com/api/public/opportunity/search-result" in url:
            return _FakeResponse(
                200,
                {
                    "data": {
                        "data": [
                            {
                                "title": "Remote Python AI Internship",
                                "seo_url": "https://unstop.com/internships/remote-python-ai-101",
                                "organisation": {"name": "Bharat AI Labs"},
                                "jobDetail": {"min_salary": 25000, "max_salary": 35000, "pay_in": "month", "type": "wfh"},
                                "regnRequirements": {"end_regn_dt": "2026-12-20T18:30:00Z"},
                                "required_skills": [{"skill_name": "Python"}, {"skill_name": "FastAPI"}],
                                "details": "<p>Build Python FastAPI microservices remotely.</p>",
                            },
                            {
                                "title": "Campus Sales Volunteer",
                                "seo_url": "https://unstop.com/internships/campus-sales-102",
                                "organisation": {"name": "Local Org"},
                                "details": "<p>Offline campus outreach.</p>",
                            },
                        ]
                    }
                },
            )
        if "remotive.com/api/remote-jobs" in url:
            return _FakeResponse(
                200,
                {
                    "jobs": [
                        {
                            "title": "Remote Python AI Internship Duplicate",
                            "url": "https://unstop.com/internships/remote-python-ai-101/",
                            "company_name": "Bharat AI Labs",
                            "salary": "INR 30,000/month",
                            "tags": ["python"],
                            "description": "Duplicate URL should be deduplicated.",
                        },
                        {
                            "title": "Backend Python Developer (Remote)",
                            "url": "https://remotive.com/remote-jobs/python-dev-201",
                            "company_name": "CloudWorks",
                            "candidate_required_location": "Worldwide Remote",
                            "salary": "$4,000/month",
                            "tags": ["python", "remote"],
                            "description": "<p>Remote backend Python role.</p>",
                        },
                    ]
                },
            )
        if "arbeitnow.com" in url:
            return _FakeResponse(200, {"data": []})
        return _FakeResponse(200, {"hackathons": []})

    monkeypatch.setattr(live_scout.requests, "get", fake_get)

    res = asyncio.run(
        live_scout.search_live_opportunities(
            query_text="मेरे लिए रिमोट पायथन इंटर्नशिप खोजो",
            user_profile={"skills": ["Python"], "location_pref": "Remote"},
            limit=5,
        )
    )
    items = res["items"]
    urls = [it["url"].rstrip("/") for it in items]

    assert len(urls) == len(set(urls))
    assert "https://unstop.com/internships/remote-python-ai-101" in urls
    assert "https://remotive.com/remote-jobs/python-dev-201" in urls
    assert items[0]["title_hi"].startswith("[HI] ")
    assert items[0]["summary_hi"].startswith("[HI] ")
    assert res["citations"]


def test_livescout_cron_poll_deduplicates_on_second_pass(isolated_db, monkeypatch):
    import tasks.cron_jobs as cron_jobs

    phone = "919876500111"
    asyncio.run(
        users_collection.insert_one(
            {
                "whatsapp_phone": phone,
                "preferred_language": "en",
                "profile_json": {"skills": ["Python"]},
                "learned_preferences": {},
            }
        )
    )
    task = {
        "yutori_task_id": "livescout_demo_1",
        "whatsapp_phone": phone,
        "query_instruction": "Remote Python Hackathon",
        "task_type": "HACKATHON",
        "task_kind": "scout",
        "status": "ACTIVE",
    }
    asyncio.run(tasks_collection.insert_one(dict(task)))

    async def fake_search_live(**kwargs):
        return {
            "raw_text": "- Live Python Hack",
            "items": [
                {
                    "title": "Live Python Hack 2026",
                    "title_hi": "लाइव पायथन हैक 2026",
                    "organization": "DevHub",
                    "category": "Hackathon",
                    "location": "Online",
                    "stipend_or_reward": "INR 1,00,000",
                    "eligibility": "Open to students",
                    "application_steps": "Register online",
                    "summary_en": "- Build Python AI agents",
                    "summary_hi": "- पायथन AI एजेंट बनाएं",
                    "url": "https://unstop.com/hackathons/live-python-hack-999",
                    "deadline_iso": "2026-12-25",
                }
            ],
            "citations": [
                {
                    "id": "1",
                    "url": "https://unstop.com/hackathons/live-python-hack-999",
                    "title": "Live Python Hack",
                }
            ],
        }

    async def noop_send(*args, **kwargs):
        return {"messages": [{"id": "wamid.mock"}]}

    monkeypatch.setattr(cron_jobs, "search_live_opportunities", fake_search_live)
    monkeypatch.setattr("services.opportunity_service.send_opportunity_card", noop_send)

    first_count = asyncio.run(poll_single_yutori_task(task))
    second_count = asyncio.run(poll_single_yutori_task(task))

    assert first_count == 1
    assert second_count == 0
    saved = asyncio.run(opportunities_collection.find({"whatsapp_phone": phone}).to_list(10))
    assert len(saved) == 1
    assert saved[0]["yutori_task_id"] == "livescout_demo_1"
    updated_task = asyncio.run(tasks_collection.find_one({"yutori_task_id": "livescout_demo_1"}))
    assert updated_task and updated_task.get("last_checked") is not None
