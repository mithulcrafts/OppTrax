import asyncio
from datetime import datetime, timedelta, timezone

from database import (
    opportunities_collection,
    tasks_collection,
    tracked_collection,
    users_collection,
    whatsapp_messages_collection,
)
from services.opportunity_service import compute_priority_score, record_learning_feedback, run_deadline_reminder_sweep


def test_yutori_webhook_bilingual_scoring_and_dedup(client, monkeypatch):
    phone = "919555111222"
    asyncio.run(
        users_collection.insert_one(
            {
                "whatsapp_phone": phone,
                "preferred_language": "both",
                "digest_mode": "instant",
                "profile_json": {"skills": ["Python", "AI"], "preferred_locations": ["Remote"]},
                "learned_preferences": {"liked_keywords": ["ai"], "disliked_keywords": []},
                "saved_opportunities": [],
                "qa_history": {},
            }
        )
    )
    asyncio.run(
        tasks_collection.insert_one(
            {
                "yutori_task_id": "scout_abc_123",
                "whatsapp_phone": phone,
                "query_instruction": "AI internships",
                "task_type": "CAREER",
                "task_kind": "scout",
                "status": "ACTIVE",
                "findings": [],
            }
        )
    )

    deadline_soon = (datetime.now(timezone.utc) + timedelta(hours=12)).isoformat()

    async def fake_parse(**kwargs):
        return [
            {
                "title": "Google STEP AI Internship",
                "title_hi": "गूगल STEP AI इंटर्नशिप",
                "organization": "Google India",
                "category": "Internship",
                "location": "Bengaluru / Remote",
                "stipend_or_reward": "INR 85,000/month",
                "eligibility": "B.Tech CS/IT students in 2nd/3rd year",
                "application_steps": "Submit resume & transcript online",
                "summary_en": "- Paid AI engineering internship at Google India.",
                "summary_hi": "- गूगल इंडिया में भुगतान वाली AI इंजीनियरिंग इंटर्नशिप।",
                "full_details_en": "Full program breakdown covering mentorship, stipend, and interview rounds.",
                "full_details_hi": "मेंटरशिप, स्टाइपेंड और इंटरव्यू राउंड की पूरी जानकारी।",
                "url": "https://careers.google.com/step-ai-2026",
                "deadline_iso": deadline_soon,
                "semantic_relevance": 0.92,
                "reasoning": "Strong match with Python and AI skills.",
            }
        ]

    monkeypatch.setattr("routers.yutori_webhook.parse_yutori_findings", fake_parse)

    payload = {
        "task_id": "scout_abc_123",
        "updates": [
            {
                "content": "Google STEP AI Internship open for applications.",
                "citations": [{"id": "1", "url": "https://careers.google.com/step-ai-2026", "title": "Google Careers"}],
            }
        ],
    }

    res = client.post("/yutori-webhook", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "processed"
    assert data["saved_count"] == 1
    opp_id = data["saved_ids"][0]

    # Duplicate webhook update must not duplicate opportunity or messages
    dup_res = client.post("/yutori-webhook", json=payload)
    assert dup_res.status_code == 200
    assert dup_res.json()["saved_count"] == 0

    opp_doc = asyncio.run(opportunities_collection.find_one({"_id": opp_id}))
    assert opp_doc is not None
    assert "Google STEP" in opp_doc["title"]
    assert "गूगल STEP" in opp_doc["title_hi"]
    assert len(opp_doc["citations"]) == 1

    wa_msgs = asyncio.run(whatsapp_messages_collection.find({"whatsapp_phone": phone}).to_list(20))
    assert any("गूगल" in m["text"] and "Google STEP" in m["text"] for m in wa_msgs)
    assert any(m.get("actions") for m in wa_msgs)


def test_tiered_deadline_reminders_and_learning(client):
    phone = "919666222333"
    asyncio.run(
        users_collection.insert_one(
            {
                "whatsapp_phone": phone,
                "preferred_language": "both",
                "learned_preferences": {"liked_keywords": [], "disliked_keywords": []},
                "saved_opportunities": [],
            }
        )
    )
    deadline_dt = datetime.now(timezone.utc) + timedelta(hours=18)
    opp_id = "opp_deadline_1"
    asyncio.run(
        opportunities_collection.insert_one(
            {
                "_id": opp_id,
                "whatsapp_phone": phone,
                "title": "Y Combinator Startup Fellowship",
                "title_hi": "वाई कॉम्बिनेटर स्टार्टअप फेलोशिप",
                "category": "Fellowship",
                "organization": "YC",
                "url": "https://www.ycombinator.com/apply",
                "deadline_iso": deadline_dt.isoformat(),
                "deadline_timestamp": deadline_dt.timestamp(),
                "created_at": datetime.now(timezone.utc).timestamp(),
            }
        )
    )

    from routers.auth import create_session_token

    token = create_session_token(phone)
    track_res = client.post(
        f"/api/opportunities/{opp_id}/track",
        json={"status": "interested"},
        headers={"X-Session-Token": token},
    )
    assert track_res.status_code == 200

    # 1st sweep triggers 24h tier
    first_sweep = asyncio.run(run_deadline_reminder_sweep(target_phone=phone))
    assert len(first_sweep) == 1
    assert first_sweep[0]["tier"] == "24h"

    wa_msgs = asyncio.run(whatsapp_messages_collection.find({"whatsapp_phone": phone}).to_list(20))
    assert any("Deadline Reminder - 24h" in m["text"] and "अंतिम तिथि रिमाइंडर" in m["text"] for m in wa_msgs)

    # Immediate 2nd sweep does not spam duplicate reminder
    second_sweep = asyncio.run(run_deadline_reminder_sweep(target_phone=phone))
    assert len(second_sweep) == 0

    # Applied stops future 6h reminders too
    asyncio.run(tracked_collection.update_one({"opportunity_id": opp_id}, {"$set": {"reminded_tiers": []}}))
    client.post(f"/api/opportunities/{opp_id}/track", json={"status": "applied"}, headers={"X-Session-Token": token})
    applied_sweep = asyncio.run(run_deadline_reminder_sweep(target_phone=phone))
    assert len(applied_sweep) == 0

    # Continuous learning score shift
    prefs = asyncio.run(record_learning_feedback(phone, opp_id, "interested"))
    score_boosted, _ = compute_priority_score(
        {"title": "Y Combinator Fellowship", "semantic_relevance": 0.6},
        task_type="CAREER",
        learned_preferences=prefs,
    )
    score_plain, _ = compute_priority_score(
        {"title": "Y Combinator Fellowship", "semantic_relevance": 0.6},
        task_type="CAREER",
        learned_preferences=None,
    )
    assert score_boosted > score_plain


def test_yutori_polling_and_daily_digest(client, monkeypatch):
    from services.opportunity_service import send_daily_digest_for_user
    from tasks.cron_jobs import poll_single_yutori_task

    phone = "919444555666"
    asyncio.run(
        users_collection.insert_one(
            {
                "whatsapp_phone": phone,
                "preferred_language": "both",
                "digest_mode": "daily",
                "profile_json": {"skills": ["Python"]},
                "learned_preferences": {"liked_keywords": [], "disliked_keywords": []},
                "saved_opportunities": [],
            }
        )
    )
    scout_task = {
        "yutori_task_id": "yut_poll_1",
        "whatsapp_phone": phone,
        "query_instruction": "Python fellowships",
        "task_type": "CAREER",
        "task_kind": "scout",
        "status": "ACTIVE",
        "findings": [],
    }
    asyncio.run(tasks_collection.insert_one(scout_task))

    async def fake_fetch_updates(task_id):
        return {
            "task_id": task_id,
            "updates": [
                {
                    "content": "PSF Python Fellowship open with $3000 stipend at https://python.org/psf/fellowship",
                    "citations": [{"id": "1", "url": "https://python.org/psf/fellowship"}],
                }
            ],
        }

    async def fake_parse_poll(**kwargs):
        return [
            {
                "title": "PSF Developer Fellowship",
                "title_hi": "PSF पायथन डेवलपर फेलोशिप",
                "organization": "Python Software Foundation",
                "category": "Fellowship",
                "location": "Remote",
                "stipend_or_reward": "$3000/month",
                "eligibility": "Open source Python contributors",
                "application_steps": "Submit proposal online",
                "summary_en": "- Global remote Python open source fellowship.",
                "summary_hi": "- ग्लोबल रिमोट पायथन ओपन सोर्स फेलोशिप।",
                "url": "https://python.org/psf/fellowship",
                "deadline_iso": "2026-12-25T18:30:00Z",
                "semantic_relevance": 0.9,
            }
        ]

    monkeypatch.setattr("tasks.cron_jobs.fetch_yutori_updates", fake_fetch_updates)
    monkeypatch.setattr("routers.yutori_webhook.parse_yutori_findings", fake_parse_poll)

    saved_count = asyncio.run(poll_single_yutori_task(scout_task))
    assert saved_count == 1

    # Since digest_mode is "daily", opp should wait until daily digest runs
    digested = asyncio.run(send_daily_digest_for_user(phone))
    assert digested == 1

    msgs = asyncio.run(whatsapp_messages_collection.find({"whatsapp_phone": phone}).to_list(20))
    assert any("Daily Opportunity Digest" in m["text"] for m in msgs)
