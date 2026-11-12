import re
import time
from typing import Any

from config import (
    COMMAND_MENU,
    GIBBERISH_PATTERNS,
    HELP_KEYWORDS,
    MAX_INPUT_LENGTH,
    MIN_INPUT_LENGTH,
    TRACKING_INTENTS,
    USER_TRACKING_CHOICES,
    integration_status,
)
from database import (
    intent_log_collection,
    opportunities_collection,
    otp_verifications_collection,
    processed_webhooks_collection,
    tasks_collection,
    tracked_collection,
    users_collection,
)
from services.ai_engine import (
    analyze_intent,
    answer_opportunity_question,
    build_user_profile,
    extract_opportunity_from_input,
    generate_application_draft,
    instant_opportunity_search,
)
from services.language import detect_query_language, language_label, normalize_language
from services.media_processor import (
    deliver_voice_alert,
    download_whatsapp_media,
    extract_opportunity_from_image,
    extract_text_from_pdf,
    transcribe_voice_note,
)
from services.opportunity_service import (
    record_learning_feedback,
    save_and_notify_opportunity,
    send_daily_digest_for_user,
    track_opportunity_for_user,
)
from services.whatsapp_api import (
    log_whatsapp_message,
    send_action_buttons,
    send_bilingual_welcome_and_onboarding,
    send_full_opportunity_details,
    send_language_selector,
    send_onboarding_choices,
    send_scout_list_menu,
    send_whatsapp_message,
)
from services.yutori_client import launch_yutori_research, launch_yutori_scout, terminate_yutori_scout

GREETING_WORDS = {"hi", "hello", "hey", "namaste", "नमस्ते", "start", "hii", "helo"}

LANGUAGE_COMMANDS = {
    "lang en": "en",
    "language en": "en",
    "english": "en",
    "en": "en",
    "switch to english": "en",
    "अंग्रेजी": "en",
    "lang hi": "hi",
    "language hi": "hi",
    "hindi": "hi",
    "hi hindi": "hi",
    "हिंदी": "hi",
    "हिंदी में": "hi",
    "switch to hindi": "hi",
    "lang both": "both",
    "language both": "both",
    "both": "both",
    "bilingual": "both",
    "दोनों": "both",
}

PRESET_LABELS = {
    "onboard_internships": ("CAREER", "Internships"),
    "onboard_jobs": ("CAREER", "Full-Time Jobs"),
    "onboard_hackathons": ("CAREER", "Hackathons"),
    "onboard_conferences": ("GENERAL", "Tech Conferences"),
    "onboard_scholarships": ("CAREER", "Scholarships"),
    "onboard_flights": ("GENERAL", "Flights BLR-DEL"),
    "onboard_vc_sf": ("GENERAL", "SF VC Seed Rounds"),
    "onboard_esoc": ("CAREER", "Euro Summer of Code"),
    "onboard_blr_events": ("GENERAL", "Bengaluru Tech Events"),
}


def _bilingual(lang: str, en_text: str, hi_text: str) -> str:
    norm = normalize_language(lang, default="both")
    if norm == "en":
        return en_text
    if norm == "hi":
        return hi_text
    return f"{en_text}\n\n{hi_text}"


async def ensure_user_doc(phone: str) -> tuple[dict[str, Any], bool]:
    """Fetches or initializes a WhatsApp user document. Returns (doc, is_new)."""
    existing = await users_collection.find_one({"whatsapp_phone": phone})
    if existing:
        return existing, False

    new_doc = {
        "whatsapp_phone": phone,
        "onboarding_status": "WELCOME_PENDING",
        "preferred_language": "both",
        "digest_mode": "instant",
        "profile_json": None,
        "saved_opportunities": [],
        "learned_preferences": {"liked_keywords": [], "disliked_keywords": [], "liked_categories": []},
        "qa_history": {},
        "created_at": time.time(),
    }
    await users_collection.insert_one(new_doc)
    return new_doc, True


async def _mark_webhook_seen(message_id: str | None, phone: str) -> bool:
    if not message_id:
        return False
    dedup_id = f"wa_{message_id}"
    if await processed_webhooks_collection.find_one({"_id": dedup_id}):
        return True
    await processed_webhooks_collection.update_one(
        {"_id": dedup_id},
        {"$set": {"phone": phone, "ts": time.time()}},
        upsert=True,
    )
    return False


async def _find_opportunity_by_id_or_prefix(phone: str, opp_ref: str) -> dict[str, Any] | None:
    ref = opp_ref.strip()
    if not ref:
        user = await users_collection.find_one({"whatsapp_phone": phone})
        last_id = (user or {}).get("last_opportunity_id")
        return await opportunities_collection.find_one({"_id": last_id}) if last_id else None

    direct = await opportunities_collection.find_one({"_id": ref})
    if direct:
        return direct

    cursor = opportunities_collection.find({"whatsapp_phone": phone}).sort("created_at", -1).limit(50)
    async for doc in cursor:
        if str(doc.get("_id", "")).startswith(ref):
            return doc
    return None


async def _set_language_preference(phone: str, lang_code: str, user: dict[str, Any]) -> str:
    norm = normalize_language(lang_code, default="both")
    await users_collection.update_one({"whatsapp_phone": phone}, {"$set": {"preferred_language": norm}})
    msg = _bilingual(
        norm,
        f"Language set to *{language_label(norm)}*. Ask me anything or search opportunities now!",
        f"भाषा *{language_label(norm)}* पर सेट हो गई है। अब आप कोई भी अवसर खोज सकते हैं या सवाल पूछ सकते हैं!",
    )
    await send_whatsapp_message(phone, msg)
    if user.get("onboarding_status") == "LANGUAGE_PENDING":
        await users_collection.update_one({"whatsapp_phone": phone}, {"$set": {"onboarding_status": "WELCOME_PENDING"}})
        await send_bilingual_welcome_and_onboarding(phone)
    return f"language_set:{norm}"


async def _handle_preset_onboard(phone: str, button_id: str, user: dict[str, Any]) -> str:
    lang = normalize_language(user.get("preferred_language"), default="both")
    task_type, label = PRESET_LABELS[button_id]
    instruction = TRACKING_INTENTS[button_id]
    profile = user.get("profile_json")

    if profile and button_id in {"onboard_internships", "onboard_jobs", "onboard_hackathons", "onboard_scholarships"}:
        skills = ", ".join(profile.get("skills", [])[:8]) or "software development"
        locs = ", ".join(profile.get("preferred_locations", [])[:4]) or "Remote / India"
        instruction = (
            f"{instruction} Tailor strictly to candidate skills ({skills}) "
            f"and locations ({locs}). Extract title, org, deadline, stipend, eligibility, and URL."
        )

    await users_collection.update_one({"whatsapp_phone": phone}, {"$set": {"onboarding_status": "COMPLETED"}})
    await _dispatch_search_and_scout(phone, label, instruction, task_type, user, lang)
    return f"onboard_preset:{button_id}"


async def _handle_scout_list_or_stop(phone: str, interactive_id: str, lang: str) -> str:
    action, tid = interactive_id.split("_", 1)
    task = await tasks_collection.find_one({"yutori_task_id": tid})
    if action == "select" and task:
        await send_action_buttons(
            to_phone=phone,
            task_id=tid,
            instruction=task.get("query_instruction", "Scout"),
            finding_count=len(task.get("findings", [])),
            is_active=(task.get("status") == "ACTIVE"),
        )
        return f"select_scout:{tid}"

    if action == "status":
        findings = (task or {}).get("findings", [])
        if not findings:
            await send_whatsapp_message(
                phone,
                _bilingual(lang, "No findings recorded yet.", "इस स्काउट के लिए अभी तक कोई अवसर नहीं मिला है।"),
            )
            return f"status_empty:{tid}"
        lines = [f"{i}. {f.get('text', '')[:180]}" for i, f in enumerate(findings[-5:], 1)]
        await send_whatsapp_message(phone, "*Recent Scout Findings:*\n" + "\n".join(lines))
        return f"status_scout:{tid}"

    if action == "stop":
        await terminate_yutori_scout(tid)
        await tasks_collection.update_one({"yutori_task_id": tid}, {"$set": {"status": "STOPPED"}})
        await send_whatsapp_message(
            phone,
            _bilingual(lang, f"Scout `{tid[:12]}` stopped.", f"स्काउट `{tid[:12]}` रोक दिया गया है।"),
        )
        return f"stop_scout:{tid}"
    return "unknown_scout_action"


async def _start_deep_research(phone: str, opp: dict[str, Any], lang: str) -> str:
    opp_id = str(opp["_id"])
    title = opp.get("title", "Opportunity")
    query = (
        f"Deep research verified details for '{title}' ({opp.get('url', '')}): "
        f"eligibility, stipend/reward, closing deadline, selection rounds, application steps, and sources."
    )
    research_id = await launch_yutori_research(query)
    is_live_sync = bool(research_id and str(research_id).startswith("livescout_res_"))
    if research_id:
        await tasks_collection.update_one(
            {"yutori_task_id": research_id},
            {
                "$set": {
                    "yutori_task_id": research_id,
                    "whatsapp_phone": phone,
                    "query_instruction": query,
                    "task_type": opp.get("category", "CAREER"),
                    "task_kind": "research",
                    "opportunity_id": opp_id,
                    "status": "COMPLETED" if is_live_sync else "ACTIVE",
                    "created_at": time.time(),
                    "last_checked": time.time() if is_live_sync else 0,
                    "findings": [],
                }
            },
            upsert=True,
        )

    await send_whatsapp_message(
        phone,
        _bilingual(lang, f"Running deep research on *{title}*...", f"*{title}* पर विस्तृत शोध किया जा रहा है..."),
    )
    user = await users_collection.find_one({"whatsapp_phone": phone})
    qa_res = await answer_opportunity_question(
        opp=opp,
        question=(
            "Give a complete structured breakdown: Eligibility, Stipend/Reward, "
            "Important Dates & Deadline, Selection Process, Application Steps, and Tips."
        ),
        user_profile=(user or {}).get("profile_json"),
        preferred_language=lang,
        force_web_search=True,
    )
    await send_whatsapp_message(phone, qa_res.get("answer", ""))
    return f"research:{opp_id}"


async def _action_track(phone: str, opp_id: str, lang: str) -> str:
    tracked = await track_opportunity_for_user(phone, opp_id, status="interested")
    if not tracked:
        await send_whatsapp_message(phone, "Opportunity not found.")
        return "track_not_found"
    await record_learning_feedback(phone, opp_id, "interested")
    opp = await opportunities_collection.find_one({"_id": opp_id})
    title = (opp or {}).get("title", opp_id)
    dl = tracked.get("deadline_iso") or "Not specified"
    await send_whatsapp_message(
        phone,
        _bilingual(
            lang,
            f"Saved & tracking *{title}* (`{opp_id}`).\nDeadline: *{dl}*\nReminders enabled (48h, 24h, 6h prior).",
            f"*{title}* सेव और ट्रैक किया गया (`{opp_id}`)।\nअंतिम तिथि: *{dl}*\nरिमाइंडर सक्रिय किए गए हैं।",
        ),
    )
    return f"tracked:{opp_id}"


async def _action_ask_mode(phone: str, opp_ref: str, lang: str) -> str:
    opp = await _find_opportunity_by_id_or_prefix(phone, opp_ref)
    if not opp:
        await send_whatsapp_message(phone, f"Opportunity `{opp_ref}` not found.")
        return "ask_not_found"
    real_id = str(opp["_id"])
    await users_collection.update_one(
        {"whatsapp_phone": phone},
        {"$set": {"active_qa_opp_id": real_id, "last_opportunity_id": real_id}},
    )
    title = opp.get("title", "this opportunity")
    en_msg = f"*Q&A Mode Active:* {title}\nAsk any question (or type `draft` for SOP). Reply `exit` when done."
    hi_msg = f"*{title}* के लिए प्रश्नोत्तर मोड चालू है।\nहिंदी या अंग्रेजी में सवाल पूछें (या `draft` लिखें)।"
    await send_whatsapp_message(phone, _bilingual(lang, en_msg, hi_msg))
    return f"ask_mode:{real_id}"


async def _action_applied_or_skip(phone: str, prefix: str, opp_id: str, lang: str) -> str:
    if prefix == "applied":
        await tracked_collection.update_one(
            {"whatsapp_phone": phone, "opportunity_id": opp_id},
            {"$set": {"status": "applied", "updated_at": time.time()}},
            upsert=True,
        )
        await record_learning_feedback(phone, opp_id, "interested")
        await send_whatsapp_message(
            phone,
            _bilingual(lang, f"Marked `{opp_id}` as *Applied*.", f"`{opp_id}` को *आवेदित* चिह्नित किया गया।"),
        )
        return f"applied:{opp_id}"
    await record_learning_feedback(phone, opp_id, "skip")
    await send_whatsapp_message(
        phone,
        _bilingual(lang, "Feedback saved. Future matches tuned.", "फीडबैक सेव हुआ। आगे बेहतर सुझाव भेजे जाएंगे।"),
    )
    return f"skip:{opp_id}"


async def _handle_opportunity_action(phone: str, interactive_id: str, user: dict[str, Any]) -> str:
    lang = normalize_language(user.get("preferred_language"), default="both")
    if "_" not in interactive_id:
        return "unknown_action"
    prefix, opp_ref = interactive_id.split("_", 1)

    if prefix in {"track", "interested"}:
        return await _action_track(phone, opp_ref, lang)
    if prefix == "details":
        opp = await _find_opportunity_by_id_or_prefix(phone, opp_ref)
        if not opp:
            await send_whatsapp_message(phone, f"Opportunity `{opp_ref}` not found.")
            return "details_not_found"
        await users_collection.update_one({"whatsapp_phone": phone}, {"$set": {"last_opportunity_id": str(opp["_id"])}})
        await send_full_opportunity_details(phone, opp, preferred_language=lang)
        return f"details:{opp['_id']}"
    if prefix == "ask":
        return await _action_ask_mode(phone, opp_ref, lang)
    if prefix == "research":
        opp = await _find_opportunity_by_id_or_prefix(phone, opp_ref)
        if not opp:
            await send_whatsapp_message(phone, f"Opportunity `{opp_ref}` not found.")
            return "research_not_found"
        return await _start_deep_research(phone, opp, lang)
    if prefix in {"applied", "skip"}:
        return await _action_applied_or_skip(phone, prefix, opp_ref, lang)
    return "unknown_action"


async def _handle_interactive(phone: str, interactive_id: str, user: dict[str, Any]) -> str:
    lang = normalize_language(user.get("preferred_language"), default="both")
    if interactive_id.startswith("lang_"):
        return await _set_language_preference(phone, interactive_id.replace("lang_", "", 1), user)
    if interactive_id == "onboard_upload":
        await users_collection.update_one({"whatsapp_phone": phone}, {"$set": {"onboarding_status": "WAITING_RESUME"}})
        await send_whatsapp_message(
            phone,
            _bilingual(lang, "Upload your *PDF Resume* or type your skills & roles.", "अपना *PDF रेज़्यूमे* या कौशल भेजें।"),
        )
        return "onboard_upload"
    if interactive_id == "onboard_choices":
        await users_collection.update_one({"whatsapp_phone": phone}, {"$set": {"onboarding_status": "CHOICE_PENDING"}})
        await send_onboarding_choices(phone)
        return "onboard_choices"
    if interactive_id in {"onboard_skip", "onboard_manual"}:
        await users_collection.update_one({"whatsapp_phone": phone}, {"$set": {"onboarding_status": "COMPLETED"}})
        await send_whatsapp_message(phone, USER_TRACKING_CHOICES.get("onboard_manual", COMMAND_MENU))
        return interactive_id
    if interactive_id in PRESET_LABELS:
        return await _handle_preset_onboard(phone, interactive_id, user)
    if interactive_id.startswith(("select_", "status_", "stop_")):
        return await _handle_scout_list_or_stop(phone, interactive_id, lang)
    return await _handle_opportunity_action(phone, interactive_id, user)


async def _process_resume_text(phone: str, resume_text: str, lang: str) -> str:
    profile = await build_user_profile(resume_text)
    if not profile:
        await send_whatsapp_message(
            phone,
            _bilingual(lang, "Could not parse profile text.", "प्रोफ़ाइल पार्स नहीं हो सकी। कृपया दोबारा प्रयास करें।"),
        )
        return "profile_parse_failed"

    await users_collection.update_one(
        {"whatsapp_phone": phone},
        {"$set": {"profile_json": profile, "raw_resume_text": resume_text[:10000], "onboarding_status": "COMPLETED"}},
    )
    skills = ", ".join(profile.get("skills", [])[:6]) or "Tech"
    roles = ", ".join(profile.get("target_roles", [])[:3]) or "Internships & Roles"
    await send_whatsapp_message(
        phone,
        _bilingual(
            lang,
            f"*Profile Activated!*\nSkills: {skills}\nRoles: {roles}\nSend a search query anytime.",
            f"*प्रोफ़ाइल सक्रिय हो गई!*\nकौशल: {skills}\nभूमिकाएं: {roles}\nअब कोई भी खोज भेजें।",
        ),
    )
    return "profile_updated"


async def _handle_document_media(phone: str, media_id: str, caption: str, user: dict[str, Any], lang: str) -> str:
    file_path = await download_whatsapp_media(media_id, ext="pdf")
    pdf_text = extract_text_from_pdf(file_path) if file_path else None
    if not pdf_text or len(pdf_text.strip()) < 40:
        await send_whatsapp_message(phone, _bilingual(lang, "Could not extract text from PDF.", "PDF पढ़ा नहीं जा सका।"))
        return "pdf_empty"
    is_resume = "resume" in caption.lower() or "cv" in caption.lower() or user.get("onboarding_status") != "COMPLETED"
    if is_resume:
        return await _process_resume_text(phone, pdf_text, lang)
    extracted = await extract_opportunity_from_input(pdf_text[:8000])
    if extracted:
        saved = await save_and_notify_opportunity(extracted, phone, raw_content=pdf_text, force_immediate=True)
        return f"pdf_opp_saved:{saved['_id']}" if saved else "pdf_opp_dup"
    return await _process_resume_text(phone, pdf_text, lang)


async def _handle_image_media(phone: str, media_id: str, mime_type: str | None, caption: str, lang: str) -> str:
    ext = "png" if mime_type and "png" in mime_type else "jpg"
    file_path = await download_whatsapp_media(media_id, ext=ext)
    items = (
        await extract_opportunity_from_image(file_path, mime_type=mime_type or "image/jpeg", caption=caption)
        if file_path
        else []
    )
    if not items:
        await send_whatsapp_message(
            phone,
            _bilingual(
                lang,
                "No actionable opportunity found in that image.",
                "इस इमेज में कोई अवसर विवरण नहीं मिला।",
            ),
        )
        return "image_no_opp"
    count = 0
    for item in items:
        raw = caption or item.get("summary_en", "")
        if await save_and_notify_opportunity(item, phone, raw_content=raw, force_immediate=True):
            count += 1
    return f"image_saved:{count}"


async def _handle_media_message(
    phone: str,
    msg_type: str,
    media_id: str | None,
    mime_type: str | None,
    caption: str,
    user: dict[str, Any],
) -> tuple[str | None, str]:
    lang = normalize_language(user.get("preferred_language"), default="both")
    if not media_id:
        return None, "missing_media_id"
    if msg_type == "audio":
        file_path = await download_whatsapp_media(media_id, ext="ogg")
        stt = await transcribe_voice_note(file_path) if file_path else None
        if not stt or not stt.get("transcript"):
            await send_whatsapp_message(phone, _bilingual(lang, "Voice unclear. Please send text.", "वॉयस समझ नहीं आया।"))
            return None, "voice_failed"
        await send_whatsapp_message(phone, f"_Heard / सुना गया:_ \"{stt['transcript']}\"")
        return stt["transcript"], "voice_transcribed"
    if msg_type == "document":
        return None, await _handle_document_media(phone, media_id, caption, user, lang)
    if msg_type == "image":
        return None, await _handle_image_media(phone, media_id, mime_type, caption, lang)
    return None, "unsupported_media"


async def _dispatch_search_and_scout(
    phone: str,
    raw_query: str,
    refined_instruction: str,
    task_type: str,
    user: dict[str, Any],
    lang: str,
) -> dict[str, Any]:
    await intent_log_collection.insert_one(
        {"whatsapp_phone": phone, "raw_input": raw_query, "intent_type": task_type, "timestamp": time.time()}
    )

    task_id = (
        await launch_yutori_scout(refined_instruction, output_interval=3600)
        or f"livescout_{phone[-6:]}_{int(time.time())}"
    )
    scout_mode = (
        "YUTORI_LIVE"
        if integration_status().get("yutori") and not str(task_id).startswith(("livescout_", "local_"))
        else "LIVESCOUT_LIVE"
    )

    await tasks_collection.insert_one(
        {
            "yutori_task_id": task_id,
            "whatsapp_phone": phone,
            "query_instruction": refined_instruction,
            "task_type": task_type,
            "task_kind": "scout",
            "status": "ACTIVE",
            "source_mode": scout_mode,
            "created_at": time.time(),
            "last_checked": 0,
            "findings": [],
        }
    )

    instant_res = await instant_opportunity_search(
        query_text=refined_instruction,
        user_profile=user.get("profile_json"),
        learned_preferences=user.get("learned_preferences"),
    )
    instant_items = instant_res.get("items") or []
    instant_citations = instant_res.get("citations") or []

    saved_instant: list[dict[str, Any]] = []
    for item in instant_items:
        doc = await save_and_notify_opportunity(
            parsed=item,
            owner_phone=phone,
            raw_content=item.get("full_details_en", item.get("summary_en", "")),
            task_id=task_id,
            task_type=task_type,
            citations=instant_citations,
            notify_whatsapp=True,
            force_immediate=True,
        )
        if doc:
            saved_instant.append(doc)

    scout_line_en = (
        f"LiveScout `{task_id[:14]}` activated ({scout_mode}) for:\n_{refined_instruction[:220]}_\n"
        f"Live matches delivered: *{len(saved_instant)}*."
    )
    scout_line_hi = f"लाइव-स्काउट `{task_id[:14]}` सक्रिय हो गया है।\nतुरंत मिले अवसर: *{len(saved_instant)}*।"
    await send_whatsapp_message(phone, _bilingual(lang, scout_line_en, scout_line_hi))
    return {"task_id": task_id, "instant_count": len(saved_instant)}


async def _handle_qa_question(phone: str, opp: dict[str, Any], question: str, user: dict[str, Any]) -> str:
    opp_id = str(opp["_id"])
    preferred_lang = normalize_language(user.get("preferred_language"), default="both")
    answer_lang = detect_query_language(question, preferred_language=preferred_lang)

    q_lower = question.strip().lower()
    if any(tok in q_lower for tok in ("draft", "cover letter", "sop", "application letter")):
        draft = await generate_application_draft(opp, user.get("profile_json"), preferred_language=answer_lang)
        await send_whatsapp_message(phone, draft)
        return f"qa_draft:{opp_id}"

    qa_map = dict(user.get("qa_history") or {})
    history = list(qa_map.get(opp_id) or [])
    qa_res = await answer_opportunity_question(
        opp=opp,
        question=question,
        user_profile=user.get("profile_json"),
        chat_history=history,
        preferred_language=answer_lang,
        force_web_search=True,
    )
    answer = qa_res.get("answer", "")
    answer_lang = qa_res.get("language", answer_lang)

    history.extend([{"role": "user", "text": question}, {"role": "assistant", "text": answer}])
    qa_map[opp_id] = history[-10:]
    await users_collection.update_one({"whatsapp_phone": phone}, {"$set": {"qa_history": qa_map}})

    await send_whatsapp_message(phone, answer)
    if "voice" in q_lower or "audio" in q_lower or "बोल" in q_lower:
        await deliver_voice_alert(phone, answer[:450], preferred_language="hi" if answer_lang == "hi" else "en")
    return f"qa_answered:{opp_id}:{answer_lang}"


async def _search_saved_board(phone: str, query: str, lang: str) -> str:
    keywords = [w.lower() for w in re.findall(r"[a-zA-Z0-9\u0900-\u097F]{2,}", query)]
    cursor = opportunities_collection.find({"whatsapp_phone": phone}).sort("created_at", -1).limit(40)
    matches: list[dict[str, Any]] = []
    async for doc in cursor:
        fields = ("title", "title_hi", "organization", "category", "summary_en", "summary_hi", "location")
        blob = " ".join(str(doc.get(k) or "") for k in fields).lower()
        if not keywords or any(kw in blob for kw in keywords):
            matches.append(doc)
        if len(matches) >= 5:
            break

    if not matches:
        await send_whatsapp_message(
            phone,
            _bilingual(
                lang,
                f"No saved opportunities matched `{query}`. Send a new search prompt!",
                f"आपके सेव बोर्ड में `{query}` से कोई अवसर नहीं मिला। नई खोज के लिए संदेश भेजें!",
            ),
        )
        return "saved_search_empty"

    lines = [
        f"*{i}. {m.get('title', 'Opportunity')}* (`{m.get('_id')}`)\n"
        f"Deadline: {m.get('deadline_iso') or 'N/A'}\n{m.get('url', '')}"
        for i, m in enumerate(matches, 1)
    ]
    header = _bilingual(lang, "*Saved Matches:*\n", "*सेव किए गए अवसर:*\n")
    await send_whatsapp_message(phone, f"{header}\n\n".join(lines) + "\n\n_Reply `details <id>` or `ask <id> <question>`_")
    return f"saved_search_hits:{len(matches)}"


async def _send_saved_board_overview(phone: str, lang: str) -> str:
    tracked = await tracked_collection.find({"whatsapp_phone": phone}).to_list(length=20)
    lines: list[str] = []
    for item in tracked[:10]:
        opp = await opportunities_collection.find_one({"_id": item.get("opportunity_id")})
        if opp:
            lines.append(
                f"• *{opp.get('title')}* (`{opp.get('_id')}`) — "
                f"Status: `{item.get('status')}` | Deadline: {opp.get('deadline_iso') or 'N/A'}"
            )
    if not lines:
        recent = await opportunities_collection.find({"whatsapp_phone": phone}).sort("created_at", -1).limit(5).to_list(5)
        for opp in recent:
            lines.append(
                f"• *{opp.get('title')}* (`{opp.get('_id')}`) — Deadline: {opp.get('deadline_iso') or 'N/A'}"
            )
    if not lines:
        await send_whatsapp_message(
            phone,
            _bilingual(lang, "Your board is empty right now. Send a search prompt!", "आपका बोर्ड अभी खाली है।"),
        )
        return "board_empty"
    hdr = _bilingual(lang, "*Your Tracked & Recent Opportunities:*", "*आपके ट्रैक किए गए और हालिया अवसर:*")
    await send_whatsapp_message(
        phone,
        f"{hdr}\n\n" + "\n".join(lines) + "\n\n_Commands: `details <id>`, `ask <id> <question>`, `research <id>`_",
    )
    return "board_listed"


async def _handle_ask_cmd(phone: str, clean: str, user: dict[str, Any]) -> str:
    parts = clean.split(None, 2)
    if len(parts) < 2:
        await send_whatsapp_message(phone, "Usage: `ask <opportunity_id> <your question>`")
        return "ask_usage"
    opp = await _find_opportunity_by_id_or_prefix(phone, parts[1])
    if not opp:
        await send_whatsapp_message(phone, f"Opportunity `{parts[1]}` not found.")
        return "ask_not_found"
    await users_collection.update_one(
        {"whatsapp_phone": phone},
        {"$set": {"active_qa_opp_id": str(opp["_id"]), "last_opportunity_id": str(opp["_id"])}},
    )
    if len(parts) == 3:
        return await _handle_qa_question(phone, opp, parts[2], user)
    return await _handle_opportunity_action(phone, f"ask_{opp['_id']}", user)


async def _handle_stop_prefix(phone: str, target: str, lang: str) -> str:
    active = await tasks_collection.find({"whatsapp_phone": phone, "status": "ACTIVE"}).to_list(length=20)
    for task in active:
        if str(task.get("yutori_task_id", "")).startswith(target) or target == "all":
            await _handle_scout_list_or_stop(phone, f"stop_{task['yutori_task_id']}", lang)
    return "stop_command"


async def _handle_draft_prefix(phone: str, clean: str, user: dict[str, Any], lang: str) -> str | None:
    ref = clean.split()[-1]
    opp = await _find_opportunity_by_id_or_prefix(phone, ref if len(ref) >= 6 else "")
    if opp:
        await send_whatsapp_message(phone, await generate_application_draft(opp, user.get("profile_json"), lang))
        return f"draft:{opp['_id']}"
    return None


async def _handle_prefix_commands(phone: str, clean: str, lower: str, user: dict[str, Any], lang: str) -> str | None:
    prefix_action_map = {"details ": "details_", "detail ": "details_", "track ": "track_", "save ": "track_"}
    for pref, action_pfx in prefix_action_map.items():
        if lower.startswith(pref):
            opp = await _find_opportunity_by_id_or_prefix(phone, clean.split(None, 1)[1])
            if opp:
                return await _handle_opportunity_action(phone, f"{action_pfx}{opp['_id']}", user)
            await send_whatsapp_message(phone, "Opportunity ID not found.")
            return f"{action_pfx}not_found"

    if lower.startswith(("research ", "deep ")):
        opp = await _find_opportunity_by_id_or_prefix(phone, clean.split(None, 1)[1])
        if opp:
            return await _start_deep_research(phone, opp, lang)
        await send_whatsapp_message(phone, "Opportunity ID not found.")
        return "research_not_found"

    if lower.startswith("ask "):
        return await _handle_ask_cmd(phone, clean, user)
    if lower.startswith(("draft ", "cover letter ", "sop ")):
        return await _handle_draft_prefix(phone, clean, user, lang)
    if lower.startswith("stop "):
        return await _handle_stop_prefix(phone, clean.split(None, 1)[1].strip(), lang)
    if lower in {"interested", "skip", "applied"} and user.get("last_opportunity_id"):
        return await _handle_opportunity_action(phone, f"{lower}_{user['last_opportunity_id']}", user)
    return None


async def _handle_digest_cmd(phone: str, lower: str, lang: str) -> str:
    if "daily" in lower:
        await users_collection.update_one({"whatsapp_phone": phone}, {"$set": {"digest_mode": "daily"}})
        await send_whatsapp_message(
            phone,
            _bilingual(lang, "Alerts set to *Daily Digest*.", "अलर्ट *दैनिक डाइजेस्ट* पर सेट हैं।"),
        )
        return "digest_daily"
    if "instant" in lower:
        await users_collection.update_one({"whatsapp_phone": phone}, {"$set": {"digest_mode": "instant"}})
        await send_whatsapp_message(phone, _bilingual(lang, "Alerts set to *Instant* mode.", "अलर्ट *तुरंत* मोड पर सेट हैं।"))
        return "digest_instant"
    sent = await send_daily_digest_for_user(phone)
    if sent == 0:
        await send_whatsapp_message(
            phone,
            _bilingual(lang, "No pending opportunities for digest.", "कोई अवसर लंबित नहीं है।"),
        )
    return f"digest_sent:{sent}"


async def _handle_text_commands(phone: str, text: str, user: dict[str, Any]) -> str | None:
    clean = text.strip()
    lower = clean.lower()
    lang = normalize_language(user.get("preferred_language"), default="both")

    if lower in LANGUAGE_COMMANDS:
        return await _set_language_preference(phone, LANGUAGE_COMMANDS[lower], user)
    if lower in HELP_KEYWORDS or lower == "menu":
        await send_whatsapp_message(phone, COMMAND_MENU)
        return "help"
    if lower in {"exit", "back", "cancel", "बंद", "वापस"} and user.get("active_qa_opp_id"):
        await users_collection.update_one({"whatsapp_phone": phone}, {"$unset": {"active_qa_opp_id": ""}})
        await send_whatsapp_message(phone, _bilingual(lang, "Exited Q&A mode.", "प्रश्नोत्तर मोड बंद हो गया।"))
        return "qa_exited"
    if lower in {"list", "scouts", "show scouts", "status", "स्काउट्स"}:
        q = {"whatsapp_phone": phone, "status": "ACTIVE", "task_kind": {"$ne": "research"}}
        active = await tasks_collection.find(q).to_list(length=10)
        if not active:
            await send_whatsapp_message(phone, _bilingual(lang, "No active scouts.", "कोई सक्रिय स्काउट नहीं है।"))
            return "scouts_empty"
        await send_scout_list_menu(phone, active)
        return f"scouts_listed:{len(active)}"
    if lower in {"saved", "tracked", "board", "bookmarks", "सेव", "ट्रैक"}:
        return await _send_saved_board_overview(phone, lang)
    if lower in {"profile", "my profile", "प्रोफ़ाइल"}:
        prof = user.get("profile_json") or {}
        skills = ", ".join(prof.get("skills", [])[:10]) or "None"
        await send_whatsapp_message(
            phone,
            f"*Profile ({language_label(lang)})*\n*Skills:* {skills}\n*Digest:* `{user.get('digest_mode', 'instant')}`",
        )
        return "profile_shown"
    if lower.startswith("digest"):
        return await _handle_digest_cmd(phone, lower, lang)
    return await _handle_prefix_commands(phone, clean, lower, user, lang)


async def _check_whatsapp_otp_verify(phone: str, clean: str, lang: str) -> str | None:
    match = re.match(r"^(?:verify|hi opptrax!\s*my code is)\s+(\d{6})$", clean, re.IGNORECASE)
    if not match:
        return None
    rec = await otp_verifications_collection.find_one({"whatsapp_phone": phone})
    if not rec or rec.get("otp") != match.group(1):
        await send_whatsapp_message(phone, _bilingual(lang, "Invalid or expired OTP.", "अमान्य या समाप्त OTP।"))
        return "otp_verify_failed"
    await otp_verifications_collection.delete_one({"whatsapp_phone": phone})
    await users_collection.update_one({"whatsapp_phone": phone}, {"$set": {"whatsapp_verified": True}})
    await send_bilingual_welcome_and_onboarding(phone)
    return "otp_verified_via_whatsapp"


async def _handle_text_message(phone: str, text: str, user: dict[str, Any], is_new: bool) -> str:
    clean = text.strip()
    lower = clean.lower()
    lang = normalize_language(user.get("preferred_language"), default="both")

    otp_status = await _check_whatsapp_otp_verify(phone, clean, lang)
    if otp_status:
        return otp_status

    cmd_result = await _handle_text_commands(phone, clean, user)
    if cmd_result:
        return cmd_result

    if lower in GREETING_WORDS or (is_new and len(clean.split()) <= 2 and not clean.startswith("http")):
        await send_bilingual_welcome_and_onboarding(phone)
        await send_language_selector(phone)
        return "welcome_sent"

    is_gibberish = any(re.search(p, clean, re.I) for p in GIBBERISH_PATTERNS)
    if not (MIN_INPUT_LENGTH <= len(clean) <= MAX_INPUT_LENGTH) or is_gibberish:
        await send_whatsapp_message(phone, "Please send a clear search query, URL, or `help`.")
        return "invalid_input"

    return await _handle_freeform_text(phone, clean, lower, user, lang)


async def _handle_freeform_text(phone: str, clean: str, lower: str, user: dict[str, Any], lang: str) -> str:
    if re.match(r"^https?://\S+$", clean, re.IGNORECASE):
        extracted = await extract_opportunity_from_input(clean)
        if extracted:
            saved = await save_and_notify_opportunity(extracted, phone, raw_content=clean, force_immediate=True)
            return f"url_saved:{saved['_id']}" if saved else "url_duplicate"
        await send_whatsapp_message(phone, _bilingual(lang, "Could not parse URL.", "लिंक से अवसर विवरण नहीं मिला।"))
        return "url_failed"

    if user.get("onboarding_status") == "WAITING_RESUME" and len(clean.split()) >= 5:
        return await _process_resume_text(phone, clean, lang)

    active_qa_id = user.get("active_qa_opp_id")
    explicit_new_search = lower.startswith(("search ", "find ", "scout ", "खोजो "))
    if active_qa_id and not explicit_new_search:
        opp = await opportunities_collection.find_one({"_id": active_qa_id})
        if opp:
            return await _handle_qa_question(phone, opp, clean, user)

    if lower.startswith(("search saved ", "find saved ")):
        return await _search_saved_board(phone, clean.split(None, 2)[2], lang)

    return await _route_discovery_query(phone, clean, explicit_new_search, user, lang)


async def _route_discovery_query(
    phone: str,
    clean: str,
    explicit_new_search: bool,
    user: dict[str, Any],
    lang: str,
) -> str:
    query = clean.split(None, 1)[1] if explicit_new_search and len(clean.split()) > 1 else clean
    routed = await analyze_intent(query, user.get("profile_json"), user.get("learned_preferences"))
    intent_kind = routed.get("intent", "CAREER")

    if intent_kind == "SEARCH_SAVED":
        return await _search_saved_board(phone, routed.get("search_keywords") or query, lang)
    if intent_kind == "INVALID":
        await send_whatsapp_message(phone, routed.get("reply") or COMMAND_MENU)
        return "intent_invalid"

    await users_collection.update_one({"whatsapp_phone": phone}, {"$set": {"onboarding_status": "COMPLETED"}})
    refined = routed.get("refined_instruction") or query
    res = await _dispatch_search_and_scout(phone, query, refined, routed.get("category") or "CAREER", user, lang)
    return f"scout_dispatched:{res['task_id']}"


async def handle_incoming_message(
    from_phone: str,
    text: str | None = None,
    interactive_id: str | None = None,
    msg_type: str = "text",
    media_id: str | None = None,
    mime_type: str | None = None,
    caption: str = "",
    message_id: str | None = None,
) -> dict[str, Any]:
    """Unified entrypoint for Meta webhook, Twilio webhook, and Website WhatsApp simulator."""
    if await _mark_webhook_seen(message_id, from_phone):
        return {"status": "duplicate", "action": "ignored"}

    user, is_new = await ensure_user_doc(from_phone)
    await log_whatsapp_message(
        phone=from_phone,
        direction="inbound",
        msg_type=msg_type,
        text=interactive_id or text or caption or f"[{msg_type}]",
        delivery_status="received",
    )

    if interactive_id:
        return {"status": "ok", "action": await _handle_interactive(from_phone, interactive_id, user)}

    if msg_type in {"audio", "document", "image"}:
        transcript, media_action = await _handle_media_message(from_phone, msg_type, media_id, mime_type, caption, user)
        if not transcript:
            return {"status": "ok", "action": media_action}
        text = transcript

    if text and text.strip():
        return {"status": "ok", "action": await _handle_text_message(from_phone, text, user, is_new)}

    return {"status": "ignored", "action": "empty"}
