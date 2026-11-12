import os
import tempfile
import time
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel

from config import integration_status
from database import (
    opportunities_collection,
    tasks_collection,
    tracked_collection,
    users_collection,
    whatsapp_messages_collection,
)
from routers.auth import resolve_authenticated_phone
from services.ai_engine import (
    analyze_intent,
    answer_opportunity_question,
    build_user_profile,
    extract_opportunity_from_input,
    generate_application_draft,
    instant_opportunity_search,
)
from services.language import detect_query_language, normalize_language
from services.media_processor import extract_text_from_pdf
from services.opportunity_service import (
    record_learning_feedback,
    run_deadline_reminder_sweep,
    save_and_notify_opportunity,
    send_daily_digest_for_user,
    track_opportunity_for_user,
)
from services.whatsapp_api import get_whatsapp_connect_link, send_opportunity_card, send_whatsapp_message
from services.whatsapp_bot import handle_incoming_message
from services.yutori_client import launch_yutori_research, launch_yutori_scout, terminate_yutori_scout

router = APIRouter(prefix="/api", tags=["opportunities"])


class ProfileUpdateRequest(BaseModel):
    preferred_language: str | None = None
    digest_mode: str | None = None
    skills: list[str] | None = None
    target_roles: list[str] | None = None
    preferred_locations: list[str] | None = None
    experience_level: str | None = None
    summary: str | None = None
    raw_resume_text: str | None = None


class SearchRequest(BaseModel):
    query: str
    launch_scout: bool = True
    send_to_whatsapp: bool = True


class IngestRequest(BaseModel):
    content: str
    notify_whatsapp: bool = True


class TrackRequest(BaseModel):
    status: str = "interested"
    reminder_hours: list[int] | None = None
    notify_whatsapp: bool = True


class FeedbackRequest(BaseModel):
    feedback: str


class AskRequest(BaseModel):
    question: str
    language: str | None = None
    draft: bool = False
    send_to_whatsapp: bool = False


class ResearchRequest(BaseModel):
    send_to_whatsapp: bool = True


class WhatsAppSimulateRequest(BaseModel):
    text: str | None = None
    interactive_id: str | None = None
    msg_type: str = "text"


async def _get_user_or_404(phone: str) -> dict[str, Any]:
    user = await users_collection.find_one({"whatsapp_phone": phone})
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    return user


async def _get_opp_or_404(opp_id: str) -> dict[str, Any]:
    opp = await opportunities_collection.find_one({"_id": opp_id})
    if not opp:
        raise HTTPException(status_code=404, detail="Opportunity not found.")
    return opp


def _merge_profile_fields(existing_profile: dict[str, Any] | None, req: ProfileUpdateRequest) -> dict[str, Any]:
    profile = dict(existing_profile or {})
    if req.skills is not None:
        profile["skills"] = [s.strip() for s in req.skills if s.strip()]
    if req.target_roles is not None:
        profile["target_roles"] = [r.strip() for r in req.target_roles if r.strip()]
    if req.preferred_locations is not None:
        profile["preferred_locations"] = [loc.strip() for loc in req.preferred_locations if loc.strip()]
    if req.experience_level is not None:
        profile["experience_level"] = req.experience_level.strip()
    if req.summary is not None:
        profile["summary"] = req.summary.strip()
    return profile


def _format_opp_with_tracking(opp: dict[str, Any], tracked_map: dict[str, dict[str, Any]]) -> dict[str, Any]:
    oid = str(opp.get("_id"))
    tr = tracked_map.get(oid)
    out = dict(opp)
    out["id"] = oid
    out["is_tracked"] = tr is not None
    out["tracking"] = (
        {
            "status": tr.get("status", "interested"),
            "reminder_hours": tr.get("reminder_hours", [48, 24, 6]),
            "reminders_sent": tr.get("reminders_sent", []),
            "deadline_iso": tr.get("deadline_iso") or opp.get("deadline_iso"),
        }
        if tr
        else None
    )
    return out


@router.get("/user/profile")
async def get_profile(phone: str = Depends(resolve_authenticated_phone)) -> dict[str, Any]:
    user = await _get_user_or_404(phone)
    opp_count = await opportunities_collection.count_documents({"whatsapp_phone": phone})
    tracked_count = await tracked_collection.count_documents({"whatsapp_phone": phone})
    active_scouts = await tasks_collection.count_documents(
        {"whatsapp_phone": phone, "status": "ACTIVE", "task_kind": {"$ne": "research"}}
    )
    return {
        "status": "ok",
        "user": {
            "whatsapp_phone": phone,
            "preferred_language": normalize_language(user.get("preferred_language"), default="both"),
            "digest_mode": user.get("digest_mode", "instant"),
            "onboarding_status": user.get("onboarding_status", "WELCOME_PENDING"),
            "profile_json": user.get("profile_json"),
            "learned_preferences": user.get("learned_preferences", {}),
            "whatsapp_verified": bool(user.get("whatsapp_verified", False)),
        },
        "stats": {
            "opportunities": opp_count,
            "tracked": tracked_count,
            "active_scouts": active_scouts,
        },
        "integrations": integration_status(),
        "whatsapp_deep_link": get_whatsapp_connect_link(),
    }


@router.put("/user/profile")
async def update_profile(
    req: ProfileUpdateRequest,
    phone: str = Depends(resolve_authenticated_phone),
) -> dict[str, Any]:
    user = await _get_user_or_404(phone)
    updates: dict[str, Any] = {}

    if req.preferred_language is not None:
        updates["preferred_language"] = normalize_language(req.preferred_language, default="both")
    if req.digest_mode in {"instant", "daily"}:
        updates["digest_mode"] = req.digest_mode

    if req.raw_resume_text and req.raw_resume_text.strip():
        built = await build_user_profile(req.raw_resume_text)
        if built:
            updates["profile_json"] = built
            updates["raw_resume_text"] = req.raw_resume_text[:10000]
            updates["onboarding_status"] = "COMPLETED"
    elif any(
        val is not None
        for val in (req.skills, req.target_roles, req.preferred_locations, req.experience_level, req.summary)
    ):
        updates["profile_json"] = _merge_profile_fields(user.get("profile_json"), req)
        updates["onboarding_status"] = "COMPLETED"

    if updates:
        await users_collection.update_one({"whatsapp_phone": phone}, {"$set": updates})

    fresh = await _get_user_or_404(phone)
    return {
        "status": "ok",
        "preferred_language": fresh.get("preferred_language", "both"),
        "digest_mode": fresh.get("digest_mode", "instant"),
        "profile_json": fresh.get("profile_json"),
        "onboarding_status": fresh.get("onboarding_status", "COMPLETED"),
    }


@router.post("/user/resume")
async def upload_resume(
    file: UploadFile = File(...),
    notify_whatsapp: bool = Form(default=True),
    phone: str = Depends(resolve_authenticated_phone),
) -> dict[str, Any]:
    user = await _get_user_or_404(phone)
    raw_bytes = await file.read()
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    filename = (file.filename or "resume.pdf").lower()
    extracted_text = ""
    if filename.endswith(".pdf"):
        fd, tmp_path = tempfile.mkstemp(suffix=".pdf")
        try:
            os.close(fd)
            with open(tmp_path, "wb") as f:
                f.write(raw_bytes)
            extracted_text = extract_text_from_pdf(tmp_path) or ""
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
    else:
        extracted_text = raw_bytes.decode("utf-8", errors="ignore")

    if len(extracted_text.strip()) < 25:
        raise HTTPException(status_code=400, detail="Could not extract readable text from resume.")

    profile = await build_user_profile(extracted_text)
    if not profile:
        raise HTTPException(status_code=500, detail="Failed to parse resume profile.")

    await users_collection.update_one(
        {"whatsapp_phone": phone},
        {"$set": {"profile_json": profile, "raw_resume_text": extracted_text[:10000], "onboarding_status": "COMPLETED"}},
    )

    if notify_whatsapp:
        lang = normalize_language(user.get("preferred_language"), default="both")
        skills = ", ".join(profile.get("skills", [])[:8]) or "Updated"
        msg = (
            f"Resume updated from web dashboard!\nSkills: {skills}"
            if lang == "en"
            else f"वेब डैशबोर्ड से रेज़्यूमे अपडेट किया गया!\nSkills: {skills}"
        )
        await send_whatsapp_message(phone, msg)

    return {"status": "ok", "profile_json": profile}


@router.post("/search")
async def search_personalized_opportunities(
    req: SearchRequest,
    phone: str = Depends(resolve_authenticated_phone),
) -> dict[str, Any]:
    clean_q = req.query.strip()
    if len(clean_q) < 3:
        raise HTTPException(status_code=400, detail="Search query must be at least 3 characters.")

    user = await _get_user_or_404(phone)
    routed = await analyze_intent(clean_q, user.get("profile_json"), user.get("learned_preferences"))
    refined = routed.get("refined_instruction") or clean_q
    category = routed.get("category") or "CAREER"

    task_id = None
    source_mode = "LIVESCOUT_INSTANT"
    if req.launch_scout:
        task_id = await launch_yutori_scout(refined, output_interval=3600) or f"livescout_{phone[-6:]}_{int(time.time())}"
        source_mode = (
            "YUTORI_LIVE"
            if integration_status().get("yutori") and not str(task_id).startswith(("livescout_", "local_"))
            else "LIVESCOUT_LIVE"
        )
        await tasks_collection.insert_one(
            {
                "yutori_task_id": task_id,
                "whatsapp_phone": phone,
                "query_instruction": refined,
                "task_type": category,
                "task_kind": "scout",
                "status": "ACTIVE",
                "source_mode": source_mode,
                "created_at": time.time(),
                "last_checked": 0,
                "findings": [],
            }
        )

    instant_res = await instant_opportunity_search(
        query_text=refined,
        user_profile=user.get("profile_json"),
        learned_preferences=user.get("learned_preferences"),
    )
    instant_items = instant_res.get("items") or []
    instant_citations = instant_res.get("citations") or []

    saved_docs: list[dict[str, Any]] = []
    for item in instant_items:
        doc = await save_and_notify_opportunity(
            parsed=item,
            owner_phone=phone,
            raw_content=item.get("full_details_en", item.get("summary_en", "")),
            task_id=task_id,
            task_type=category,
            citations=instant_citations,
            notify_whatsapp=req.send_to_whatsapp,
            force_immediate=True,
        )
        if doc:
            saved_docs.append(doc)

    if not saved_docs:
        existing_cursor = opportunities_collection.find({"whatsapp_phone": phone}).sort("created_at", -1).limit(10)
        q_terms = [t.lower() for t in clean_q.split() if len(t) > 2]
        async for doc in existing_cursor:
            blob = f"{doc.get('title', '')} {doc.get('summary_en', '')} {doc.get('category', '')}".lower()
            if not q_terms or any(t in blob for t in q_terms):
                saved_docs.append(doc)

    return {
        "status": "ok",
        "task_id": task_id,
        "source_mode": source_mode,
        "refined_instruction": refined,
        "count": len(saved_docs),
        "opportunities": [{**d, "id": str(d["_id"])} for d in saved_docs],
    }


@router.get("/scouts")
async def list_scouts(phone: str = Depends(resolve_authenticated_phone)) -> dict[str, Any]:
    docs = await tasks_collection.find({"whatsapp_phone": phone}).sort("created_at", -1).to_list(length=50)
    return {"status": "ok", "scouts": docs}


@router.delete("/scouts/{task_id}")
async def stop_scout(task_id: str, phone: str = Depends(resolve_authenticated_phone)) -> dict[str, Any]:
    task = await tasks_collection.find_one({"yutori_task_id": task_id, "whatsapp_phone": phone})
    if not task:
        raise HTTPException(status_code=404, detail="Scout not found.")
    await terminate_yutori_scout(task_id)
    await tasks_collection.update_one({"yutori_task_id": task_id}, {"$set": {"status": "STOPPED"}})
    return {"status": "ok", "task_id": task_id, "scout_status": "STOPPED"}


@router.get("/opportunities")
async def list_opportunities(
    q: str = Query(default=""),
    category: str = Query(default=""),
    tracked_only: bool = Query(default=False),
    phone: str = Depends(resolve_authenticated_phone),
) -> dict[str, Any]:
    docs = await opportunities_collection.find({"whatsapp_phone": phone}).sort("created_at", -1).to_list(length=100)
    tracked_docs = await tracked_collection.find({"whatsapp_phone": phone}).to_list(length=100)
    tracked_map = {str(t.get("opportunity_id")): t for t in tracked_docs}

    q_lower = q.strip().lower()
    cat_lower = category.strip().lower()
    results = []
    for doc in docs:
        oid = str(doc.get("_id"))
        if tracked_only and oid not in tracked_map:
            continue
        if cat_lower and cat_lower != "all" and str(doc.get("category", "")).lower() != cat_lower:
            continue
        if q_lower:
            blob = " ".join(
                str(doc.get(k) or "")
                for k in ("title", "title_hi", "organization", "location", "summary_en", "summary_hi", "category")
            ).lower()
            if q_lower not in blob:
                continue
        results.append(_format_opp_with_tracking(doc, tracked_map))

    return {"status": "ok", "count": len(results), "opportunities": results}


@router.get("/opportunities/{opp_id}")
async def get_opportunity_detail(opp_id: str, phone: str = Depends(resolve_authenticated_phone)) -> dict[str, Any]:
    opp = await _get_opp_or_404(opp_id)
    if opp.get("whatsapp_phone") and opp.get("whatsapp_phone") != phone:
        raise HTTPException(status_code=403, detail="Not authorized for this opportunity.")

    user = await _get_user_or_404(phone)
    tracked = await tracked_collection.find_one({"whatsapp_phone": phone, "opportunity_id": opp_id})
    tracked_map = {opp_id: tracked} if tracked else {}
    qa_history = (user.get("qa_history") or {}).get(opp_id, [])

    return {
        "status": "ok",
        "opportunity": _format_opp_with_tracking(opp, tracked_map),
        "qa_history": qa_history,
    }


@router.post("/opportunities/ingest")
async def ingest_opportunity(req: IngestRequest, phone: str = Depends(resolve_authenticated_phone)) -> dict[str, Any]:
    if not req.content.strip():
        raise HTTPException(status_code=400, detail="Content or URL is required.")
    parsed = await extract_opportunity_from_input(req.content.strip())
    if not parsed:
        raise HTTPException(status_code=422, detail="Could not extract valid opportunity from input.")
    saved = await save_and_notify_opportunity(
        parsed=parsed,
        owner_phone=phone,
        raw_content=req.content,
        notify_whatsapp=req.notify_whatsapp,
        force_immediate=True,
    )
    if not saved:
        raise HTTPException(status_code=409, detail="Opportunity already exists or was filtered.")
    return {"status": "ok", "opportunity": {**saved, "id": str(saved["_id"])}}


@router.post("/opportunities/{opp_id}/track")
async def track_opportunity(
    opp_id: str,
    req: TrackRequest,
    phone: str = Depends(resolve_authenticated_phone),
) -> dict[str, Any]:
    opp = await _get_opp_or_404(opp_id)
    tracked = await track_opportunity_for_user(
        phone=phone,
        opp_id=opp_id,
        status=req.status,
        reminder_hours=req.reminder_hours,
    )
    await record_learning_feedback(phone, opp_id, "interested")

    if req.notify_whatsapp:
        user = await _get_user_or_404(phone)
        lang = normalize_language(user.get("preferred_language"), default="both")
        title = opp.get("title", opp_id)
        dl = (tracked or {}).get("deadline_iso") or "Not specified"
        msg = (
            f"Tracking *{title}* (Status: `{req.status}`). Deadline reminders active for {dl}."
            if lang == "en"
            else f"*{title}* ट्रैक किया गया (स्थिति: `{req.status}`)। अंतिम तिथि ({dl}) से पहले रिमाइंडर सक्रिय हैं।"
        )
        await send_whatsapp_message(phone, msg)

    return {"status": "ok", "tracking": tracked}


@router.post("/opportunities/{opp_id}/feedback")
async def opportunity_feedback(
    opp_id: str,
    req: FeedbackRequest,
    phone: str = Depends(resolve_authenticated_phone),
) -> dict[str, Any]:
    await _get_opp_or_404(opp_id)
    prefs = await record_learning_feedback(phone, opp_id, req.feedback)
    return {"status": "ok", "learned_preferences": prefs}


@router.post("/opportunities/{opp_id}/ask")
async def ask_opportunity_question_api(
    opp_id: str,
    req: AskRequest,
    phone: str = Depends(resolve_authenticated_phone),
) -> dict[str, Any]:
    if not req.question.strip() and not req.draft:
        raise HTTPException(status_code=400, detail="Question is required.")

    opp = await _get_opp_or_404(opp_id)
    user = await _get_user_or_404(phone)
    pref_lang = normalize_language(req.language or user.get("preferred_language"), default="both")
    target_lang = req.language if req.language in {"en", "hi", "both"} else detect_query_language(req.question, pref_lang)

    qa_map = dict(user.get("qa_history") or {})
    history = list(qa_map.get(opp_id) or [])

    if req.draft:
        answer = await generate_application_draft(opp, user.get("profile_json"), preferred_language=target_lang)
        user_msg = req.question.strip() or "Draft an application / cover letter"
    else:
        user_msg = req.question.strip()
        qa_res = await answer_opportunity_question(
            opp=opp,
            question=user_msg,
            user_profile=user.get("profile_json"),
            chat_history=history,
            preferred_language=target_lang,
            force_web_search=True,
        )
        answer = qa_res.get("answer", "")
        target_lang = qa_res.get("language", target_lang)

    history.extend([{"role": "user", "text": user_msg}, {"role": "assistant", "text": answer}])
    qa_map[opp_id] = history[-16:]
    await users_collection.update_one(
        {"whatsapp_phone": phone},
        {"$set": {"qa_history": qa_map, "last_opportunity_id": opp_id}},
    )

    if req.send_to_whatsapp:
        await send_whatsapp_message(phone, f"*{opp.get('title', 'OppTrax Q&A')}*\n\n{answer}")

    return {"status": "ok", "language": target_lang, "answer": answer, "qa_history": qa_map[opp_id]}


@router.post("/opportunities/{opp_id}/research")
async def research_opportunity_api(
    opp_id: str,
    req: ResearchRequest,
    phone: str = Depends(resolve_authenticated_phone),
) -> dict[str, Any]:
    opp = await _get_opp_or_404(opp_id)
    user = await _get_user_or_404(phone)
    lang = normalize_language(user.get("preferred_language"), default="both")

    query = (
        f"Deep research complete verified details for '{opp.get('title')}' ({opp.get('url')}): "
        f"eligibility, stipend/reward, exact deadline, selection rounds, application process, and official sources."
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

    qa_res = await answer_opportunity_question(
        opp=opp,
        question=(
            "Provide an exhaustive, verified breakdown covering: Eligibility, Compensation/Prize, "
            "Selection Rounds & Syllabus, Key Dates & Deadline, Step-by-Step Application Guide, and Tips."
        ),
        user_profile=user.get("profile_json"),
        preferred_language=lang,
        force_web_search=True,
    )
    deep_report = qa_res.get("answer", "")

    if req.send_to_whatsapp:
        await send_whatsapp_message(phone, f"*Deep Research: {opp.get('title')}*\n\n{deep_report}")

    return {
        "status": "ok",
        "research_id": research_id,
        "yutori_research_id": research_id,
        "report": deep_report,
        "language": lang,
    }


@router.post("/opportunities/{opp_id}/send-whatsapp")
async def send_opp_to_whatsapp(opp_id: str, phone: str = Depends(resolve_authenticated_phone)) -> dict[str, Any]:
    opp = await _get_opp_or_404(opp_id)
    user = await _get_user_or_404(phone)
    lang = normalize_language(user.get("preferred_language"), default="both")
    await send_opportunity_card(
        to_phone=phone,
        finding_id=opp_id,
        title=opp.get("title", "Opportunity"),
        title_hi=opp.get("title_hi"),
        summary=opp.get("summary_en") or opp.get("summary") or "",
        summary_hi=opp.get("summary_hi"),
        organization=opp.get("organization"),
        location=opp.get("location"),
        stipend_or_reward=opp.get("stipend_or_reward"),
        eligibility=opp.get("eligibility"),
        application_steps=opp.get("application_steps"),
        full_details_en=opp.get("full_details_en"),
        full_details_hi=opp.get("full_details_hi"),
        url=opp.get("url", ""),
        task_type=opp.get("category", "CAREER"),
        priority_score=opp.get("priority_score"),
        reasoning=opp.get("reasoning"),
        deadline=opp.get("deadline_iso"),
        preferred_language=lang,
        send_full_brief=True,
    )
    return {"status": "ok", "sent_to": phone, "opportunity_id": opp_id}


@router.post("/reminders/check")
async def trigger_deadline_check(phone: str = Depends(resolve_authenticated_phone)) -> dict[str, Any]:
    sent = await run_deadline_reminder_sweep(target_phone=phone)
    return {"status": "ok", "reminders_sent": sent}


@router.post("/digest/send")
async def trigger_digest_send(phone: str = Depends(resolve_authenticated_phone)) -> dict[str, Any]:
    sent = await send_daily_digest_for_user(phone)
    return {"status": "ok", "digested_count": sent}


@router.get("/whatsapp/messages")
async def get_whatsapp_messages(
    limit: int = Query(default=60, ge=1, le=200),
    phone: str = Depends(resolve_authenticated_phone),
) -> dict[str, Any]:
    docs = await whatsapp_messages_collection.find({"whatsapp_phone": phone}).sort("timestamp", -1).to_list(length=limit)
    docs.reverse()
    return {"status": "ok", "messages": docs}


@router.post("/whatsapp/simulate")
async def simulate_whatsapp_inbound(
    req: WhatsAppSimulateRequest,
    phone: str = Depends(resolve_authenticated_phone),
) -> dict[str, Any]:
    if not req.text and not req.interactive_id:
        raise HTTPException(status_code=400, detail="Either text or interactive_id is required.")
    res = await handle_incoming_message(
        from_phone=phone,
        text=req.text,
        interactive_id=req.interactive_id,
        msg_type="interactive" if req.interactive_id else req.msg_type,
    )
    return {"status": "ok", "simulation": res}
