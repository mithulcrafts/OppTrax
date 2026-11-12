import asyncio

from database import otp_verifications_collection


def _authenticate_test_user(client, phone="+91 9777888999", preferred_language="both"):
    client.post("/api/auth/send-otp", json={"phone": phone})
    sanitized = "919777888999"
    rec = asyncio.run(otp_verifications_collection.find_one({"whatsapp_phone": sanitized}))
    res = client.post(
        "/api/auth/verify-otp",
        json={"phone": phone, "otp": rec["otp"], "preferred_language": preferred_language},
    )
    assert res.status_code == 200
    return res.json()["session_token"], sanitized


def test_full_website_yutori_search_qa_hindi_english_e2e(client, monkeypatch):
    token, phone = _authenticate_test_user(client)
    headers = {"X-Session-Token": token}

    # 1. Update personalized profile
    prof_res = client.put(
        "/api/user/profile",
        headers=headers,
        json={
            "skills": ["Python", "FastAPI", "LLM Agents"],
            "target_roles": ["AI Engineer Intern"],
            "preferred_locations": ["Remote", "Bengaluru"],
            "preferred_language": "both",
        },
    )
    assert prof_res.status_code == 200
    assert "Python" in prof_res.json()["profile_json"]["skills"]

    # 2. Upload TXT resume via API
    resume_res = client.post(
        "/api/user/resume",
        headers=headers,
        files={"file": ("resume.txt", b"Skills: Python, React, FastAPI. Built AI agents. BTech CS student.", "text/plain")},
        data={"notify_whatsapp": "true"},
    )
    assert resume_res.status_code == 200
    assert resume_res.json()["profile_json"] is not None

    # 3. Mock Yutori Scout + Instant Discovery and execute /api/search
    async def fake_yutori_scout(instruction, output_interval=3600):
        return "yutori_scout_test_1"

    async def fake_instant_search(query_text, user_profile=None, learned_preferences=None):
        return {
            "raw_text": "OpenAI Residency found.",
            "items": [
                {
                    "title": "Sarvam AI Engineering Fellowship",
                    "title_hi": "सर्वम AI इंजीनियरिंग फेलोशिप",
                    "organization": "Sarvam AI",
                    "category": "Fellowship",
                    "location": "Bengaluru / Hybrid",
                    "stipend_or_reward": "INR 1,00,000/month",
                    "eligibility": "Indian engineers building multilingual AI systems",
                    "application_steps": "Submit GitHub portfolio & problem statement online",
                    "summary_en": "- Build Indic voice and LLM agents in Bengaluru.",
                    "summary_hi": "- बेंगलुरु में इंडिक वॉयस और LLM एजेंट बनाएं।",
                    "full_details_en": "6-month immersive AI fellowship working on Hindi & Indic voice models.",
                    "full_details_hi": "हिंदी और भारतीय भाषाओं के AI मॉडल पर 6 महीने की फेलोशिप।",
                    "url": "https://www.sarvam.ai/careers/fellowship",
                    "deadline_iso": "2026-12-20T18:30:00Z",
                    "semantic_relevance": 0.95,
                    "reasoning": "Matches Python and LLM Agents skills.",
                }
            ],
            "citations": [{"id": "1", "url": "https://www.sarvam.ai/careers/fellowship", "title": "Sarvam AI"}],
        }

    monkeypatch.setattr("services.whatsapp_api.YUTORI_KEY", "test_key", raising=False)
    monkeypatch.setattr("routers.opportunities.launch_yutori_scout", fake_yutori_scout)
    monkeypatch.setattr("routers.opportunities.instant_opportunity_search", fake_instant_search)
    monkeypatch.setattr("routers.opportunities.integration_status", lambda: {
        "whatsapp_meta": False,
        "whatsapp_twilio": False,
        "yutori": True,
        "gemini": False,
        "sarvam": False,
        "public_webhook_url": None,
    })

    search_res = client.post(
        "/api/search",
        headers=headers,
        json={"query": "Find Indic AI fellowships matching my profile", "launch_scout": True, "send_to_whatsapp": True},
    )
    assert search_res.status_code == 200
    s_data = search_res.json()
    assert s_data["count"] == 1
    assert s_data["task_id"] == "yutori_scout_test_1"
    opp_id = s_data["opportunities"][0]["id"]

    # 4. Ask Q&A in Hindi -> receives Hindi Devanagari reply
    hi_qa = client.post(
        f"/api/opportunities/{opp_id}/ask",
        headers=headers,
        json={"question": "इसकी पात्रता और स्टाइपेंड क्या है?", "send_to_whatsapp": True},
    )
    assert hi_qa.status_code == 200
    assert hi_qa.json()["language"] == "hi"
    assert "सर्वम" in hi_qa.json()["answer"]

    # 5. Ask Q&A in English + Draft Cover Letter
    en_qa = client.post(
        f"/api/opportunities/{opp_id}/ask",
        headers=headers,
        json={"question": "What is the stipend and location?", "language": "en"},
    )
    assert en_qa.status_code == 200
    assert en_qa.json()["language"] == "en"
    assert "Sarvam AI" in en_qa.json()["answer"]

    draft_qa = client.post(
        f"/api/opportunities/{opp_id}/ask",
        headers=headers,
        json={"question": "Draft my application", "language": "en", "draft": True},
    )
    assert draft_qa.status_code == 200
    assert "Application Strategy" in draft_qa.json()["answer"]

    # 6. Deep Research triggers Yutori Research Task and returns full report
    async def fake_yutori_research(query, output_schema=None):
        return "yutori_res_999"

    monkeypatch.setattr("routers.opportunities.launch_yutori_research", fake_yutori_research)
    res_report = client.post(
        f"/api/opportunities/{opp_id}/research",
        headers=headers,
        json={"send_to_whatsapp": True},
    )
    assert res_report.status_code == 200
    assert res_report.json()["yutori_research_id"] == "yutori_res_999"

    # 7. Simulate WhatsApp interactive button clicks & bilingual commands
    sim_track = client.post("/api/whatsapp/simulate", headers=headers, json={"interactive_id": f"track_{opp_id}"})
    assert sim_track.status_code == 200
    assert sim_track.json()["simulation"]["action"] == f"tracked:{opp_id}"

    sim_ask = client.post("/api/whatsapp/simulate", headers=headers, json={"text": f"ask {opp_id}"})
    assert sim_ask.status_code == 200
    assert sim_ask.json()["simulation"]["action"] == f"ask_mode:{opp_id}"

    sim_followup = client.post("/api/whatsapp/simulate", headers=headers, json={"text": "अंतिम तिथि कब है?"})
    assert sim_followup.status_code == 200
    assert ":hi" in sim_followup.json()["simulation"]["action"]

    # Check board reflects tracked status and WhatsApp messages contain full bilingual stream
    detail_res = client.get(f"/api/opportunities/{opp_id}", headers=headers)
    assert detail_res.status_code == 200
    assert detail_res.json()["opportunity"]["is_tracked"] is True

    wa_log = client.get("/api/whatsapp/messages?limit=50", headers=headers)
    assert wa_log.status_code == 200
    texts = "\n".join(m["text"] for m in wa_log.json()["messages"])
    assert "Sarvam AI Engineering Fellowship" in texts
    assert "सर्वम AI इंजीनियरिंग फेलोशिप" in texts

    # 8. Direct opportunity text ingestion + saved filter search
    async def fake_extract(raw_input):
        return {
            "title": "AWS AI Builders Grant",
            "title_hi": "AWS AI बिल्डर्स ग्रांट",
            "organization": "AWS",
            "category": "Grant",
            "location": "Global",
            "stipend_or_reward": "$5000 Credits",
            "eligibility": "Startups and student builders",
            "application_steps": "Apply with project demo",
            "summary_en": "- Cloud credits and AI mentorship grant.",
            "summary_hi": "- क्लाउड क्रेडिट और AI मेंटरशिप अनुदान।",
            "url": "https://aws.amazon.com/startups/ai-grant",
            "deadline_iso": "2026-12-30T18:30:00Z",
            "semantic_relevance": 0.85,
        }

    monkeypatch.setattr("routers.opportunities.extract_opportunity_from_input", fake_extract)
    ingest_res = client.post(
        "/api/opportunities/ingest",
        headers=headers,
        json={"content": "https://aws.amazon.com/startups/ai-grant AWS AI grant", "notify_whatsapp": True},
    )
    assert ingest_res.status_code == 200
    assert ingest_res.json()["opportunity"]["title"] == "AWS AI Builders Grant"

    filtered = client.get("/api/opportunities?q=AWS", headers=headers)
    assert filtered.status_code == 200
    assert filtered.json()["count"] == 1

    sim_saved_search = client.post("/api/whatsapp/simulate", headers=headers, json={"text": "search saved AWS"})
    assert sim_saved_search.status_code == 200
    assert "saved_search_hits" in sim_saved_search.json()["simulation"]["action"]
