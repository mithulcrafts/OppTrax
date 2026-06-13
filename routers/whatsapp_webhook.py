import os
import time
from datetime import datetime, timezone
from fastapi import APIRouter, Request, HTTPException, BackgroundTasks
from fastapi.responses import PlainTextResponse

from config import WHATSAPP_VERIFY_TOKEN, MIN_INPUT_LENGTH, MAX_INPUT_LENGTH, GIBBERISH_PATTERNS, HELP_KEYWORDS, COMMAND_MENU
from database import users_collection, tasks_collection, opportunities_collection, tracked_collection
from services.whatsapp_api import _post_whatsapp, send_whatsapp_message, send_onboarding_choices, send_scout_list_menu
from services.media_processor import download_whatsapp_media, extract_text_from_pdf, transcribe_voice_note
from services.ai_engine import build_user_profile, analyze_intent, gemini_client, GATEKEEPER_MODEL
from services.yutori_client import launch_yutori_scout, terminate_yutori_scout
from google.genai import types

router = APIRouter()

def fast_reject(text: str) -> str | None:
    cleaned = text.strip().lower()
    if len(cleaned) < MIN_INPUT_LENGTH:
        return "Input too short. Describe what you want to track or monitor."
    if len(cleaned) > MAX_INPUT_LENGTH:
        return "Input too long. Keep your request under a few sentences."
    if not any(c.isalpha() for c in cleaned):
        return "No readable text detected. Send a clear tracking request."
    for pattern in GIBBERISH_PATTERNS:
        if cleaned == pattern or (len(cleaned) < 8 and pattern in cleaned):
            return "Input looks like a test string. Send a real monitoring request."
    return None

@router.get("/")
def home():
    return {"status": "Server is running!", "version": "5.0 Modular", "engine": "OppTrax Universal"}

@router.get("/webhook")
def verify_whatsapp(request: Request):
    hub_mode = request.query_params.get("hub.mode")
    hub_verify_token = request.query_params.get("hub.verify_token")
    hub_challenge = request.query_params.get("hub.challenge")
    if hub_mode == "subscribe" and hub_verify_token == WHATSAPP_VERIFY_TOKEN:
        return PlainTextResponse(content=hub_challenge)
    raise HTTPException(status_code=403, detail="Verification token mismatch")

@router.post("/webhook")
async def receive_whatsapp_message(request: Request, background_tasks: BackgroundTasks):
    payload = await request.json()
    background_tasks.add_task(process_whatsapp_payload, payload)
    return {"status": "success"}

async def process_whatsapp_payload(payload: dict):
    try:
        entry = payload.get("entry", [{}])[0]
        change = entry.get("changes", [{}])[0]
        value = change.get("value", {})
        if "messages" not in value:
            return {"status": "success"}

        message_obj = value["messages"][0]
        sender_phone = message_obj["from"]
        msg_type = message_obj.get("type")

        # Get or create user
        user = await users_collection.find_one({"whatsapp_phone": sender_phone})
        if not user:
            await users_collection.insert_one({
                "whatsapp_phone": sender_phone,
                "profile_json": None,
                "onboarding_state": "AWAITING_RESUME_DECISION",
                "chat_context_id": None
            })
            welcome_text = (
                "*Welcome to OppTrax Universal Agent!*\n\n"
                "I am your autonomous web scouting companion. You can use these commands anytime:\n"
                "- *list* - View all active scouts\n"
                "- *stop <Task ID>* - Stop a scout\n"
                "- *help* - Show the command menu\n\n"
                "*Examples of what I can track:*\n"
                "- \"Notify me as soon as a new AI startup funding seed round gets announced.\"\n"
                "- \"Track new VC seed rounds announced in SF.\"\n"
                "- \"Notify when new projects get added to European Summer of Code with a summary.\"\n"
                "- \"Track free tech conferences in Bengaluru.\"\n\n"
                "*Would you like to enable personalized career matching?* "
                "Uploading a resume allows me to filter and score internships/jobs against your skills."
            )
            _post_whatsapp({
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": sender_phone,
                "type": "interactive",
                "interactive": {
                    "type": "button",
                    "body": {"text": welcome_text},
                    "action": {
                        "buttons": [
                            {"type": "reply", "reply": {"id": "onboard_upload", "title": "Upload Resume"}},
                            {"type": "reply", "reply": {"id": "onboard_skip", "title": "Skip for Now"}}
                        ]
                    }
                }
            })
            return {"status": "success"}

        user_profile = user.get("profile_json")
        user_state = user.get("onboarding_state")
        active_chat_id = user.get("chat_context_id")

        # =============================================================
        #  INTERACTIVE CALLBACKS
        # =============================================================
        if msg_type == "interactive":
            interactive_obj = message_obj["interactive"]
            inter_type = interactive_obj.get("type")

            if inter_type == "list_reply":
                raw_id = interactive_obj["list_reply"]["id"]
                if raw_id.startswith("onboard_"):
                    track_type = raw_id.replace("onboard_", "")
                    
                    if track_type == "manual":
                        send_whatsapp_message(sender_phone, "*Manual Mode Active*. I will only deploy scouts when you specifically ask me to. Type your request whenever you're ready!")
                    else:
                        skills_str = ", ".join(user_profile.get("skills", [])) if user_profile else "tech"
                        target_map = {
                            "internships": f"software engineering and tech internships requiring: {skills_str}",
                            "jobs": f"full-time software and tech roles requiring: {skills_str}",
                            "hackathons": f"hackathons, open source bounties, and coding competitions requiring: {skills_str}",
                            "conferences": "tech conferences, developer meetups, and summits",
                            "scholarships": "international scholarships, fellowships, and grants for tech & computer science students",
                            "flights": "flight ticket prices for routes from Bengaluru (BLR) to Delhi (DEL)",
                            "vc_sf": "new venture capital seed rounds and funding announcements in San Francisco (SF)",
                            "esoc": "new organization and project additions to European Summer of Code",
                            "blr_events": "free tech conferences, coding meetups, workshops, and summits to attend in Bengaluru"
                        }
                        target = target_map.get(track_type, "opportunities")
                        
                        # Custom instructions and categories
                        if track_type in ["internships", "jobs", "hackathons"]:
                            instruction = f"Find newly posted {target}. Extract the deadline and application URL."
                            task_category = "CAREER"
                        elif track_type == "flights":
                            instruction = "Track flight prices from Bengaluru (BLR) to Delhi (DEL) and find deals below 5000 INR."
                            task_category = "GENERAL"
                        elif track_type == "vc_sf":
                            instruction = "Track and monitor any new venture capital seed rounds or funding announcements in San Francisco (SF)."
                            task_category = "GENERAL"
                        elif track_type == "esoc":
                            instruction = "Monitor new projects, organization additions, and applications for the European Summer of Code."
                            task_category = "GENERAL"
                        elif track_type == "blr_events":
                            instruction = "Track all free tech conferences, developer meetups, workshops, and summits to attend in Bengaluru."
                            task_category = "GENERAL"
                        elif track_type == "scholarships":
                            instruction = "Track newly announced international scholarships, fellowships, and funding opportunities for tech and computer science students."
                            task_category = "GENERAL"
                        else:
                            instruction = f"Find newly posted {target}. Extract the deadline and application URL."
                            task_category = "GENERAL"
                        
                        send_whatsapp_message(sender_phone, f"Deploying scout to track: {target}...")
                        
                        scout_id = launch_yutori_scout(instruction)
                        if scout_id:
                            await tasks_collection.insert_one({
                                "yutori_task_id": scout_id, 
                                "whatsapp_number": sender_phone, 
                                "query_instruction": instruction,
                                "task_type": task_category,
                                "status": "active", 
                                "findings": [], 
                                "created_at": time.time()
                            })
                            send_whatsapp_message(sender_phone, "Task added, will respond when relevant opportunities are found.")
                else:
                    selected_id = raw_id.replace("select_", "")
                    from services.whatsapp_api import send_action_buttons
                    record = await tasks_collection.find_one({"yutori_task_id": selected_id})
                    if record:
                        finding_count = len(record.get("findings", []))
                        is_active = (record.get("status") == "active")
                        send_action_buttons(sender_phone, selected_id, record["query_instruction"], finding_count, is_active)

            elif inter_type == "button_reply":
                button_id = interactive_obj["button_reply"]["id"]

                # Onboarding Handlers
                if button_id == "onboard_upload":
                    await users_collection.update_one({"whatsapp_phone": sender_phone}, {"$set": {"onboarding_state": "AWAITING_RESUME_UPLOAD"}})
                    send_whatsapp_message(sender_phone, "Great! Please upload your resume PDF document now.")
                    return {"status": "success"}
                elif button_id == "onboard_skip":
                    await users_collection.update_one({"whatsapp_phone": sender_phone}, {"$set": {"onboarding_state": "COMPLETED"}})
                    send_whatsapp_message(sender_phone, "Setup complete! You can now ask me to track anything (e.g. 'Notify me as soon as a new AI startup funding seed round gets announced').\n\n_Note: You can still upload a resume PDF at any time later to enable career scoring!_")
                    return {"status": "success"}

                # Task Control Handlers
                elif button_id.startswith("status_"):
                    target_id = button_id.replace("status_", "")
                    
                    record = await tasks_collection.find_one({"yutori_task_id": target_id})
                    if record:
                        findings_list = record.get("findings", [])
                        if not findings_list:
                            send_whatsapp_message(sender_phone, "This scout hasn't detected any matches yet. Polling runs autonomously every 3 minutes.")
                        else:
                            from services.ai_engine import enrich_findings_with_websearch
                            send_whatsapp_message(sender_phone, "[RESEARCH] Analyzing and researching findings...")
                            summary = enrich_findings_with_websearch(findings_list[-5:])
                            send_whatsapp_message(sender_phone, summary)
                elif button_id.startswith("stop_"):
                    target_id = button_id.replace("stop_", "")
                    if terminate_yutori_scout(target_id):
                        await tasks_collection.update_one({"yutori_task_id": target_id}, {"$set": {"status": "stopped", "stopped_at": time.time()}})
                        send_whatsapp_message(sender_phone, f"[TERMINATED] Scout shut down.\nTask ID: {target_id}")
                    else:
                        send_whatsapp_message(sender_phone, "Termination request failed at Yutori endpoint. Try again.")

                # Opportunity Copilot Handlers
                elif button_id.startswith("track_"):
                    target_id = button_id.replace("track_", "")
                    await users_collection.update_one({"whatsapp_phone": sender_phone}, {"$addToSet": {"saved_opportunities": target_id}})
                    
                    opp = await opportunities_collection.find_one({"_id": target_id})
                    if opp:
                        await tracked_collection.update_one(
                            {"whatsapp_phone": sender_phone, "opportunity_id": target_id},
                            {"$set": {"deadline_iso": opp.get("deadline_iso"), "reminded": False, "tracked_at": time.time()}},
                            upsert=True
                        )
                    
                    send_whatsapp_message(sender_phone, "[SAVE] Saved to your Tracking Board! I will automatically send a reminder before any deadline closes. Type 'saved' to view your items.")
                elif button_id.startswith("ask_"):
                    target_id = button_id.replace("ask_", "")
                    opp = await opportunities_collection.find_one({"_id": target_id})
                    if opp:
                        await users_collection.update_one({"whatsapp_phone": sender_phone}, {"$set": {"chat_context_id": target_id}})
                        send_whatsapp_message(sender_phone, f"[CHAT] *AI Chat Mode Activated* for:\n*{opp.get('title')}*\n\nAsk me any questions about this based on the scraped content (e.g. 'What is the stipend?').\n\n_Type 'exit' to return to normal commands._")

            return {"status": "success"}

        # =============================================================
        #  DOCUMENT UPLOAD (RESUME INGESTION)
        # =============================================================
        if msg_type == "document":
            doc = message_obj["document"]
            if doc.get("mime_type") == "application/pdf":
                send_whatsapp_message(sender_phone, "[DOC] PDF received! Reading your resume to update your career profile...")
                file_path = download_whatsapp_media(doc["id"], "pdf")
                if file_path:
                    raw_text = extract_text_from_pdf(file_path)
                    os.remove(file_path)
                    if raw_text:
                        profile = build_user_profile(raw_text)
                        if profile:
                            await users_collection.update_one(
                                {"whatsapp_phone": sender_phone},
                                {"$set": {"profile_json": profile, "onboarding_state": "COMPLETED"}}
                            )
                            welcome_msg = f"*Career Profile Locked In!*\n\n*Skills Detected:* {', '.join(profile.get('skills', []))}\n\nType *help* anytime to see available commands.\n\nNow, let's deploy your first agent!"
                            send_whatsapp_message(sender_phone, welcome_msg)
                            send_onboarding_choices(sender_phone)
                        else:
                            send_whatsapp_message(sender_phone, "Failed to extract skills from the PDF. Please try a different format.")
            return {"status": "success"}

        # Handle remaining onboarding strict states
        if user_state == "AWAITING_RESUME_DECISION":
            text_val = message_obj.get("text", {}).get("body", "").strip().lower() if msg_type == "text" else ""
            if text_val in ["skip", "cancel"]:
                await users_collection.update_one({"whatsapp_phone": sender_phone}, {"$set": {"onboarding_state": "COMPLETED"}})
                send_whatsapp_message(sender_phone, "Setup complete! You can now ask me to track anything (e.g. 'Notify me as soon as a new AI startup funding seed round gets announced').\n\n_Note: You can still upload a resume PDF at any time later to enable career scoring!_")
            else:
                welcome_text = (
                    "*Welcome to OppTrax Universal Agent!*\n\n"
                    "I am your autonomous web scouting companion. You can use these commands anytime:\n"
                    "- *list* - View all active scouts\n"
                    "- *stop <Task ID>* - Stop a scout\n"
                    "- *help* - Show the command menu\n\n"
                    "*Examples of what I can track:*\n"
                    "- \"Notify me as soon as a new AI startup funding seed round gets announced.\"\n"
                    "- \"Track new VC seed rounds announced in SF.\"\n"
                    "- \"Notify when new projects get added to European Summer of Code with a summary.\"\n"
                    "- \"Track free tech conferences in Bengaluru.\"\n\n"
                    "*Would you like to enable personalized career matching?* "
                    "Uploading a resume allows me to filter and score internships/jobs against your skills."
                )
                _post_whatsapp({
                    "messaging_product": "whatsapp",
                    "recipient_type": "individual",
                    "to": sender_phone,
                    "type": "interactive",
                    "interactive": {
                        "type": "button",
                        "body": {"text": welcome_text},
                        "action": {
                            "buttons": [
                                {"type": "reply", "reply": {"id": "onboard_upload", "title": "Upload Resume"}},
                                {"type": "reply", "reply": {"id": "onboard_skip", "title": "Skip for Now"}}
                            ]
                        }
                    }
                })
            return {"status": "success"}
        elif user_state == "AWAITING_RESUME_UPLOAD":
            text_val = message_obj.get("text", {}).get("body", "").strip().lower() if msg_type == "text" else ""
            if text_val in ["skip", "cancel"]:
                await users_collection.update_one({"whatsapp_phone": sender_phone}, {"$set": {"onboarding_state": "COMPLETED"}})
                send_whatsapp_message(sender_phone, "Setup complete! Running as general agent.")
            else:
                send_whatsapp_message(sender_phone, "Please upload your resume PDF now. Or type 'skip' to proceed without one.")
            return {"status": "success"}

        # =============================================================
        #  TEXT AND AUDIO INPUT PARSING
        # =============================================================
        target_text = None

        if msg_type == "audio":
            media_id = message_obj["audio"]["id"]
            send_whatsapp_message(sender_phone, "[RECEIVED] Voice note captured. Processing now...")
            file_path = download_whatsapp_media(media_id)
            if file_path:
                target_text = transcribe_voice_note(file_path)
                os.remove(file_path)
                if target_text:
                    send_whatsapp_message(sender_phone, f"[TRANSCRIPT] {target_text}")
        elif msg_type == "text":
            target_text = message_obj["text"]["body"].strip()

        if not target_text:
            return {"status": "success"}

        # =============================================================
        #  CONVERSATIONAL RAG MODE (If active)
        # =============================================================
        if active_chat_id:
            if target_text.lower() == "exit":
                await users_collection.update_one({"whatsapp_phone": sender_phone}, {"$set": {"chat_context_id": None}})
                send_whatsapp_message(sender_phone, "Left chat mode. Send a new tracking command!")
                return {"status": "success"}
            
            opp = await opportunities_collection.find_one({"_id": active_chat_id})
            if opp:
                target_lower = target_text.lower()
                # --- AUTO-DRAFT APPLICATION PIPELINE INTEGRATION ---
                if "draft" in target_lower or "cover letter" in target_lower or "application strategy" in target_lower:
                    send_whatsapp_message(sender_phone, "[DRAFT] Constructing customized application assets...")
                    draft_prompt = f"""
                    Cross-reference the User Resume: {user_profile} with this Scraped Opportunity Content: {opp.get('raw_content')}
                    
                    Compile an elite, tailored application strategy package containing:
                    1. A customized Cover Letter.
                    2. Strategic bullet-points on how to position their explicit skills to beat recruiters.
                    
                    Format the response cleanly for a WhatsApp message. Keep it concise, use emojis sparingly, and avoid heavy markdown headers.
                    """
                    res = gemini_client.models.generate_content(model=GATEKEEPER_MODEL, contents=draft_prompt)
                    send_whatsapp_message(sender_phone, res.text)
                    return {"status": "success"}

                rag_prompt = f"You are OppTrax AI Assistant. Your task is to comprehensively answer the user's question about the following scraped opportunity:\n{opp.get('raw_content', '')}\n\nCRITICAL INSTRUCTION: You MUST use Google Search to cross-reference this opportunity, find missing details (like exact stipends, deadlines, official links, or company reputation), and ensure your answer is fully up-to-date and accurate before replying.\nQuestion: {target_text}"
                try:
                    res = gemini_client.models.generate_content(
                        model=GATEKEEPER_MODEL, 
                        contents=rag_prompt,
                        config=types.GenerateContentConfig(
                            tools=[{"google_search": {}}]
                        )
                    )
                    send_whatsapp_message(sender_phone, f"{res.text}\n\n_[Type 'exit' to leave chat]_")
                except Exception as e:
                    send_whatsapp_message(sender_phone, "Sorry, I couldn't process your question right now.")
            else:
                await users_collection.update_one({"whatsapp_phone": sender_phone}, {"$set": {"chat_context_id": None}})
                send_whatsapp_message(sender_phone, "Context lost. Left chat mode.")
            return {"status": "success"}

        # Normalize commands (e.g. /help -> help)
        normalized_text = target_text.strip().lower()
        if normalized_text.startswith("/"):
            normalized_text = normalized_text[1:]

        # =============================================================
        #  FAST INTERCEPTS
        # =============================================================
        if normalized_text in HELP_KEYWORDS:
            send_whatsapp_message(sender_phone, COMMAND_MENU)
            return {"status": "success"}

        if normalized_text.startswith("stop "):
            target_id = target_text.strip().split(maxsplit=1)[1].strip()
            record = await tasks_collection.find_one({"whatsapp_number": sender_phone, "yutori_task_id": target_id, "status": "active"})
            if record:
                if terminate_yutori_scout(target_id):
                    await tasks_collection.update_one({"yutori_task_id": target_id}, {"$set": {"status": "stopped", "stopped_at": time.time()}})
                    send_whatsapp_message(sender_phone, "[TERMINATED] Scout shut down successfully.")
                else:
                    send_whatsapp_message(sender_phone, "Termination request failed at Yutori endpoint. Try again.")
            else:
                send_whatsapp_message(sender_phone, "Invalid Task ID or scout not running.")
            return {"status": "success"}
            
        if normalized_text in ["list", "status", "board", "saved"]:
            if normalized_text in ["saved", "board"]:
                saved = await tracked_collection.find({"whatsapp_phone": sender_phone}).to_list(length=20)
                if not saved:
                    send_whatsapp_message(sender_phone, "*Your Tracking Board is empty.* Save opportunities to your board to get automatic deadline reminders!")
                else:
                    board_msg = "*Your Tracking Board*\n\n"
                    for idx, item in enumerate(saved, 1):
                        opp = await opportunities_collection.find_one({"_id": item["opportunity_id"]})
                        if opp:
                            dl = item.get("deadline_iso", "N/A")
                            if dl and dl != "N/A" and dl != "null":
                                try:
                                    dt = datetime.fromisoformat(dl.replace("Z", "+00:00"))
                                    dl = dt.strftime("%B %d, %Y")
                                except: pass
                            board_msg += f"{idx}. *{opp.get('title')}*\nDeadline: {dl}\nLink: {opp.get('url')}\n\n"
                    send_whatsapp_message(sender_phone, board_msg)
            else:
                active_scouts = await tasks_collection.find({"whatsapp_number": sender_phone, "status": "active"}).to_list(length=10)
                if not active_scouts:
                    send_whatsapp_message(sender_phone, "No active scouts running under your profile.")
                else:
                    send_scout_list_menu(sender_phone, active_scouts)
            return {"status": "success"}

        # Reject bad inputs before hitting LLM
        rejection = fast_reject(target_text)
        if rejection:
            send_whatsapp_message(sender_phone, rejection)
            return {"status": "success"}

        # =============================================================
        #  GATEKEEPER ROUTING
        # =============================================================
        analysis = analyze_intent(target_text, user_profile)
        intent = analysis.get("intent")
        
        if analysis.get("reply"):
            send_whatsapp_message(sender_phone, analysis.get("reply"))

        if intent == "INVALID":
            pass # handled by the reply

        elif intent == "STOP":
            active_scouts = await tasks_collection.find({"whatsapp_number": sender_phone, "status": "active"}).to_list(length=10)
            if not active_scouts:
                send_whatsapp_message(sender_phone, "No active scouts running under your profile.")
            else:
                send_scout_list_menu(sender_phone, active_scouts)

        elif intent in ["CAREER", "GENERAL"]:
            refined_instruction = analysis.get("refined_instruction")
            task_category = analysis.get("category", intent)
            if not refined_instruction:
                send_whatsapp_message(sender_phone, "Could not build a valid search query. Be more specific about what to track.")
                return {"status": "success"}

            existing = await tasks_collection.find_one({"whatsapp_number": sender_phone, "query_instruction": refined_instruction, "status": "active"})
            if existing:
                send_whatsapp_message(sender_phone, "You already have an active scout with a matching query tracking this.")
                return {"status": "success"}

            scout_id = launch_yutori_scout(refined_instruction)
            if scout_id:
                await tasks_collection.insert_one({
                    "yutori_task_id": scout_id,
                    "whatsapp_number": sender_phone,
                    "query_instruction": refined_instruction,
                    "task_type": task_category,
                    "status": "active",
                    "findings": [],
                    "created_at": time.time()
                })
                send_whatsapp_message(sender_phone, "Task added, will respond when relevant opportunities are found.")
            else:
                send_whatsapp_message(sender_phone, "Scout deployment failed. Check server logs.")

    except Exception as e:
        print(f"Webhook Error: {e}")
    return {"status": "success"}
