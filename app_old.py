from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse
import requests
import uvicorn
import os
import json
import time
import base64
import dns.resolver
import fitz  # PyMuPDF
import uuid
import asyncio
from contextlib import asynccontextmanager

# Configure dnspython to use Google DNS for robust MongoDB SRV resolution
dns.resolver.default_resolver = dns.resolver.Resolver(configure=False)
dns.resolver.default_resolver.nameservers = ['8.8.8.8']

from datetime import datetime, timezone, timedelta
from sarvamai import SarvamAI
from google import genai
from google.genai import types
from motor.motor_asyncio import AsyncIOMotorClient



# --- LIVE CONFIGURATION ---
import os
from dotenv import load_dotenv

load_dotenv()

WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN")
NGROK_BASE_URL = os.getenv("NGROK_BASE_URL")

SARVAM_KEY = os.getenv("SARVAM_KEY")
GEMINI_KEY = os.getenv("GEMINI_KEY")
YUTORI_KEY = os.getenv("YUTORI_KEY")
MONGO_URI = os.getenv("MONGO_URI")

# --- MONGO DATABASE CONFIGURATION ---
mongo_client = AsyncIOMotorClient(MONGO_URI)
db = mongo_client["opptrax_db"]
tasks_collection = db["active_tasks"]
intent_log_collection = db["intent_logs"]
users_collection = db["users"]
opportunities_collection = db["opportunities"]
tracked_collection = db["tracked_board"]

# =====================================================================
#  --- NEW FEATURE 1: AUTONOMOUS DEADLINE REMINDER BACKGROUND LOOP ---
# =====================================================================
async def deadline_reminder_loop():
    """Runs in the background, checking DB for approaching deadlines to ping users."""
    print("[SYSTEM] Background Reminder Loop Started!", flush=True)
    while True:
        try:
            now = datetime.now(timezone.utc)
            # Look ahead for deadlines happening in the next 24 to 48 hours
            warning_window = now + timedelta(days=2)
            
            # Find tracked items where deadline exists, is approaching, and haven't been reminded
            cursor = tracked_collection.find({
                "deadline_iso": {"$ne": None, "$gte": now.isoformat(), "$lte": warning_window.isoformat()},
                "reminded": {"$ne": True}
            })
            
            async for track_item in cursor:
                phone = track_item["whatsapp_phone"]
                opp = await opportunities_collection.find_one({"_id": track_item["opportunity_id"]})
                
                if opp:
                    msg = f"[ALERT] *Nexus Deadline Reminder!*\n\nYour tracked opportunity *{opp.get('title')}* is closing soon!\n\nDon't forget to apply:\n{opp.get('url')}"
                    send_whatsapp_message(phone, msg)
                    
                    # Mark as reminded so we don't spam them
                    await tracked_collection.update_one({"_id": track_item["_id"]}, {"$set": {"reminded": True}})
                    print(f"[SUCCESS] Reminder sent to {phone} for task {opp.get('title')}", flush=True)
                    
        except Exception as e:
            print(f"Reminder Loop Error: {e}", flush=True)
            
        await asyncio.sleep(3600) # Wait 1 hour before checking again

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start the background checking loop when the server boots
    task = asyncio.create_task(deadline_reminder_loop())
    yield
    task.cancel()

app = FastAPI(lifespan=lifespan)

# --- CLIENT INITIALIZATION ---
sarvam_client = SarvamAI(api_subscription_key=SARVAM_KEY)
gemini_client = genai.Client(api_key=GEMINI_KEY)

GATEKEEPER_MODEL = "gemini-2.5-flash"

# --- PRE-FILTER CONFIG ---
MIN_INPUT_LENGTH = 3
MAX_INPUT_LENGTH = 500
GIBBERISH_PATTERNS = {"asdf", "qwer", "zxcv", "jkl;", "1234", "test", "aaa", "bbb", "xxx", "zzz"}

HELP_KEYWORDS = {"help", "menu", "commands", "options", "what can you do", "how to use"}

# --- BUILT-IN COMMANDS REFERENCE ---
COMMAND_MENU = """
🤖 *Nexus Command Center*

Here is what you can tell me to do:
🔹 *list* - View all your currently deployed scouts.
🔹 *stop <Task ID>* - Terminate a specific scout.
🔹 *help* - Show this menu again.

*Examples of what I can track:*
👉 "Notify me when RCB wins the match."
👉 "Track flight prices from Mumbai to Dubai."
👉 "Find remote Web3 internships."
"""

HELP_MESSAGE = (
    "*Nexus Universal Agent & Career Copilot*\n\n"
    "Send a voice note or text describing what to track, monitor, or schedule, and an autonomous scout agent will be deployed.\n\n"
    "*Commands:*\n"
    "- _track..._ / _monitor..._ / _find..._ — deploy a scout agent\n"
    "- _list_ or _status_ — view your active scouts\n"
    "- _STOP <task_id>_ — terminate a specific scout\n"
    "- _saved_ or _board_ — view your saved opportunities\n"
    "- _help_ — show this reference\n\n"
    "*Examples:*\n"
    "- \"Notify me whenever a new movie gets added to IMDb\" (General)\n"
    "- \"Alert me if the price of iPhone 16 on Amazon drops below 70k\" (General)\n"
    "- \"Find ML internships in Bangalore with stipend above 15k\" (Career)"
)

# =====================================================================
#  WHATSAPP OUTBOUND MESSAGING
# =====================================================================

def _post_whatsapp(data: dict):
    url = f"https://graph.facebook.com/v20.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    try:
        res = requests.post(url, headers=headers, json=data, timeout=10)
        if res.status_code >= 400:
            print(f"WhatsApp API Error {res.status_code}: {res.text}", flush=True)
    except Exception as e:
        print(f"WhatsApp send error: {e}", flush=True)

def send_whatsapp_message(to_phone: str, text_content: str):
    _post_whatsapp({
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to_phone,
        "type": "text",
        "text": {"preview_url": False, "body": text_content}
    })

def send_scout_list_menu(to_phone: str, active_tasks: list):
    rows = []
    for task in active_tasks[:10]:
        title = task["query_instruction"]
        if len(title) > 24:
            title = title[:21] + "..."
        tid = task["yutori_task_id"]
        finding_count = len(task.get("findings", []))
        rows.append({
            "id": f"select_{tid}",
            "title": title,
            "description": f"{finding_count} findings | {tid[:10]}..."
        })

    _post_whatsapp({
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to_phone,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "header": {"type": "text", "text": "Nexus Control Panel"},
            "body": {"text": "Select a scout from the list below to inspect findings or manage execution."},
            "footer": {"text": f"{len(active_tasks)} active scout(s)"},
            "action": {
                "button": "View Scouts",
                "sections": [{"title": "Active Scouts", "rows": rows}]
            }
        }
    })

def send_action_buttons(to_phone: str, task_id: str, instruction: str, finding_count: int):
    _post_whatsapp({
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to_phone,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": f"*Scout Detail*\n\nQuery: _{instruction}_\nFindings collected: {finding_count}\n\nChoose an action:"},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": f"status_{task_id}", "title": "View Findings"}},
                    {"type": "reply", "reply": {"id": f"stop_{task_id}", "title": "Stop Scout"}}
                ]
            }
        }
    })

def send_opportunity_card(to_phone: str, finding_id: str, title: str, summary: str, url: str, task_type: str, priority_score: float = None, reasoning: str = None):
    """Generates the enterprise-grade interactive UI card with explicit action telemetry."""
    if task_type == "CAREER" and priority_score is not None:
        card_text = f"[MATCH] *Nexus Career Match Decided*\n\n*Role:* {title}\n*Priority Match Score:* {int(priority_score * 100)}/100\n*Structural Breakdown:* _{reasoning}_\n\n*Summary:* {summary}"
    else:
        card_text = f"[UPDATE] *Nexus Live Event Triggered*\n\n*Target:* {title}\n*Status Update:* {summary}"

    buttons = [
        {"type": "reply", "reply": {"id": f"apply_{finding_id}", "title": "View Link"}},
        {"type": "reply", "reply": {"id": f"track_{finding_id}", "title": "Save to Board"}},
        {"type": "reply", "reply": {"id": f"ask_{finding_id}", "title": "Ask AI Questions"}}
    ]

    _post_whatsapp({
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to_phone,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": card_text},
            "action": {"buttons": buttons}
        }
    })

def send_onboarding_choices(to_phone: str):
    """Sends a rich interactive list asking the user what to monitor after uploading a resume."""
    rows = [
        {"id": "onboard_internships", "title": "👨‍💻 Internships", "description": "Track internships matching my skills"},
        {"id": "onboard_hackathons", "title": "🏆 Hackathons", "description": "Find open source & hackathons"},
        {"id": "onboard_jobs", "title": "💼 Full-Time Roles", "description": "Monitor entry/mid level jobs"},
        {"id": "onboard_conferences", "title": "🎤 Conferences", "description": "Find tech meetups & summits"},
        {"id": "onboard_manual", "title": "🛑 Manual Mode Only", "description": "I will tell you what to track manually"}
    ]
    _post_whatsapp({
        "messaging_product": "whatsapp", "recipient_type": "individual", "to": to_phone,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "header": {"type": "text", "text": "🎯 Select Your Tracking Mode"},
            "body": {"text": "I can automatically deploy a Scout based on your extracted skills. What would you like me to monitor?"},
            "footer": {"text": "You can change this anytime."},
            "action": {"button": "Choose an Option", "sections": [{"title": "Autonomous Deployment", "rows": rows}]}
        }
    })

# =====================================================================
#  VOICE ENGINE (Sarvam TTS -> Meta Media Upload -> WhatsApp Audio)
# =====================================================================

def generate_sarvam_audio(text_to_speak: str) -> str | None:
    print(f"[TTS] Synthesizing voice for: {text_to_speak[:80]}...", flush=True)
    url = "https://api.sarvam.ai/text-to-speech"
    payload = {
        "inputs": [text_to_speak[:500]],
        "target_language_code": "en-IN",
        "speaker": "anushka",
        "model": "bulbul:v2",
        "output_audio_codec": "mp3"
    }
    headers = {"api-subscription-key": SARVAM_KEY, "Content-Type": "application/json"}
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=15)
        if response.status_code == 200:
            audio_base64 = response.json()["audios"][0]
            file_path = f"nexus_alert_{int(time.time())}.mp3"
            with open(file_path, "wb") as f:
                f.write(base64.b64decode(audio_base64))
            print(f"[TTS] Audio saved to {file_path}", flush=True)
            return file_path
    except Exception as e:
        print(f"[TTS] Request failed: {e}", flush=True)
    return None

def upload_whatsapp_media(file_path: str) -> str | None:
    print(f"[UPLOAD] Sending {file_path} to Meta servers...", flush=True)
    url = f"https://graph.facebook.com/v20.0/{PHONE_NUMBER_ID}/media"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}
    try:
        with open(file_path, "rb") as f:
            files = {
                "file": ("alert.mp3", f, "audio/mpeg"),
                "type": (None, "audio"),
                "messaging_product": (None, "whatsapp")
            }
            response = requests.post(url, headers=headers, files=files, timeout=15)
        if response.status_code == 200:
            return response.json().get("id")
    except Exception as e:
        print(f"[UPLOAD] Request failed: {e}", flush=True)
    return None

def send_whatsapp_audio(to_phone: str, media_id: str):
    _post_whatsapp({
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to_phone,
        "type": "audio",
        "audio": {"id": media_id}
    })

def deliver_voice_alert(to_phone: str, alert_text: str):
    audio_path = generate_sarvam_audio(alert_text)
    if not audio_path: return
    try:
        media_id = upload_whatsapp_media(audio_path)
        if media_id:
            send_whatsapp_audio(to_phone, media_id)
    finally:
        if os.path.exists(audio_path):
            os.remove(audio_path)

# =====================================================================
#  RESUME INGESTION & USER PROFILE
# =====================================================================

def extract_text_from_pdf(file_path: str) -> str | None:
    try:
        doc = fitz.open(file_path)
        text = ""
        for page in doc:
            text += page.get_text()
        return text
    except Exception as e:
        print(f"PDF Parse Error: {e}")
        return None

def build_user_profile(raw_text: str):
    prompt = f"""You are an expert tech recruiter and career counselor. Analyze this resume text and extract key profile information into JSON format.
    Resume Text:
    {raw_text}
    Format:
    {{
        "skills": ["Python", "React", "AWS", etc],
        "experience_level": "Student/Junior/Mid/Senior",
        "interests": ["Frontend", "Backend", "AI", "Mobile", etc],
        "summary": "A 2-sentence professional overview of their technical strengths."
    }}"""
    try:
        res = gemini_client.models.generate_content(
            model=GATEKEEPER_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json")
        )
        return json.loads(res.text.strip())
    except Exception as e:
        print(f"Error building user profile: {e}")
        return None

# =====================================================================
#  INPUT VALIDATION AND INTELLIGENCE
# =====================================================================

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

def analyze_intent(raw_text: str, user_profile: dict = None) -> dict:
    profile_context = f"\nUSER PROFILE: {user_profile}" if user_profile else "\nUSER PROFILE: None uploaded."
    
    prompt = f"""You are Nexus, a Universal Autonomous Agent.
    
    User Input: "{raw_text}" {profile_context}
    
    Classify the task into ONE of these categories:
    1. "START": User wants to track, search, or monitor something. 
       -> Sub-category "CAREER": Jobs, internships, hackathons, open source, scholarships. (Use the user profile context to optimize the search query).
       -> Sub-category "GENERAL": Flight prices, tennis courts, IPL sports scores, weather, product availability, etc. (IGNORE the user profile entirely. Do not constrain this search based on skills).
    2. "STOP": User wants to cancel an agent or list their agents.
    3. "INVALID": User is asking a conversational question, chitchat, or sending generic text without an actionable tracking intent.
    
    Rules for 'refined_instruction':
    - If CAREER, instruct the crawler to find the opportunity and return a summary and application URL.
    - If GENERAL, instruct the crawler to monitor the specific data (e.g., 'Monitor flight prices for X') and alert immediately upon changes.

    Return EXACT JSON format:
    {{
        "intent": "START" or "STOP" or "INVALID",
        "category": "CAREER" or "GENERAL" or null,
        "refined_instruction": "The optimized Yutori instruction (null if STOP/INVALID)",
        "reply": "Conversational confirmation of what you are about to do (e.g., 'Deploying a scout to track flight prices...') or error message if INVALID"
    }}
    """
    try:
        response = gemini_client.models.generate_content(
            model=GATEKEEPER_MODEL, 
            contents=prompt, 
            config=types.GenerateContentConfig(response_mime_type="application/json")
        )
        return json.loads(response.text.strip())
    except Exception as e:
        print(f"Gatekeeper error: {e}")
        return {"intent": "INVALID", "category": None, "refined_instruction": None, "reply": "I couldn't process that command. Try rephrasing."}

# =====================================================================
#  EXTERNAL SERVICE INTEGRATIONS
# =====================================================================

def download_whatsapp_media(media_id: str, ext: str = "ogg"):
    url = f"https://graph.facebook.com/v20.0/{media_id}"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}
    try:
        response = requests.get(url, headers=headers, timeout=15)
        if response.status_code != 200:
            return None
        media_url = response.json().get("url")
        file_response = requests.get(media_url, headers=headers, timeout=15)
        if file_response.status_code == 200:
            file_path = f"temp_media_{media_id}.{ext}"
            with open(file_path, "wb") as f:
                f.write(file_response.content)
            return file_path
    except Exception as e:
        print(f"Media Download Error: {e}")
    return None

def transcribe_voice_note(file_path: str):
    try:
        with open(file_path, "rb") as audio_file:
            response = sarvam_client.speech_to_text.transcribe(
                file=audio_file,
                model="saaras:v3",
                mode="translate",
                language_code="hi-IN"
            )
            return response.transcript
    except Exception as e:
        print(f"Sarvam Failed: {e}")
        return None

def launch_yutori_scout(instruction: str):
    url = "https://api.yutori.com/v1/scouting/tasks"
    headers = {"X-API-Key": YUTORI_KEY, "Content-Type": "application/json"}
    data = {
        "query": instruction,
        "output_interval": 3600,
        "webhook_url": f"{NGROK_BASE_URL}/yutori-webhook"
    }
    try:
        response = requests.post(url, headers=headers, json=data)
        if response.status_code in [200, 201]:
            return response.json().get("id")
    except Exception as e:
        print(f"Yutori request exception: {e}", flush=True)
    return None

def terminate_yutori_scout(task_id: str) -> bool:
    url = f"https://api.yutori.com/v1/scouting/tasks/{task_id}"
    headers = {"X-API-Key": YUTORI_KEY}
    try:
        res = requests.delete(url, headers=headers)
        return res.status_code in [200, 204]
    except Exception as e:
        print(f"Yutori DELETE failed: {e}")
        return False

# =====================================================================
#  ROUTE HANDLERS
# =====================================================================

@app.get("/")
def home():
    return {"status": "Server is running!", "version": "4.0", "engine": "Nexus Universal"}

@app.get("/webhook")
def verify_whatsapp(request: Request):
    hub_mode = request.query_params.get("hub.mode")
    hub_verify_token = request.query_params.get("hub.verify_token")
    hub_challenge = request.query_params.get("hub.challenge")
    if hub_mode == "subscribe" and hub_verify_token == WHATSAPP_VERIFY_TOKEN:
        return PlainTextResponse(content=hub_challenge)
    raise HTTPException(status_code=403, detail="Verification token mismatch")

@app.post("/webhook")
async def receive_whatsapp_message(request: Request):
    payload = await request.json()
    try:
        entry = payload.get("entry", [{}])[0]
        change = entry.get("changes", [{}])[0]
        value = change.get("value", {})

        if "messages" not in value:
            return {"status": "success"}

        message_obj = value["messages"][0]
        sender_phone = message_obj["from"]
        msg_type = message_obj.get("type")

        # =============================================================
        #  USER STATE MANAGEMENT & ONBOARDING
        # =============================================================
        user = await users_collection.find_one({"whatsapp_phone": sender_phone})
        if not user:
            # First interaction
            await users_collection.insert_one({
                "whatsapp_phone": sender_phone,
                "onboarding_state": "AWAITING_RESUME_DECISION",
                "profile_json": None,
                "chat_context_id": None,
                "saved_opportunities": [],
                "created_at": time.time()
            })
            _post_whatsapp({
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": sender_phone,
                "type": "interactive",
                "interactive": {
                    "type": "button",
                    "body": {"text": "[HELLO] *Welcome to Nexus Universal Agent!*\n\nI can track *anything* on the internet for you—from flight prices and IPL scores to internships and hackathons.\n\n*Would you like to enable personalized career matching?* Uploading a resume allows me to analyze and score jobs/internships against your skills."},
                    "action": {
                        "buttons": [
                            {"type": "reply", "reply": {"id": "onboard_upload", "title": "Upload Resume"}},
                            {"type": "reply", "reply": {"id": "onboard_skip", "title": "Skip for Now"}}
                        ]
                    }
                }
            })
            return {"status": "success"}

        user_state = user.get("onboarding_state", "COMPLETED")
        user_profile = user.get("profile_json")
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
                        send_whatsapp_message(sender_phone, "🛑 *Manual Mode Active*. I will only deploy scouts when you specifically ask me to. Type your request whenever you're ready!")
                    else:
                        skills_str = ", ".join(user_profile.get("skills", [])) if user_profile else "tech"
                        target_map = {
                            "internships": "software engineering and tech internships",
                            "hackathons": "hackathons, open source bounties, and coding competitions",
                            "jobs": "full-time software and tech roles",
                            "conferences": "tech conferences, developer meetups, and summits"
                        }
                        target = target_map.get(track_type, "opportunities")
                        instruction = f"Find newly posted {target} that specifically require these skills: {skills_str}. Extract the deadline and application URL."
                        
                        send_whatsapp_message(sender_phone, f"🚀 Deploying an autonomous scout to monitor the web for *{target}* matching your skills...")
                        
                        scout_id = launch_yutori_scout(instruction)
                        if scout_id:
                            await tasks_collection.insert_one({
                                "yutori_task_id": scout_id, 
                                "whatsapp_number": sender_phone, 
                                "query_instruction": instruction,
                                "task_type": "CAREER",
                                "status": "active", 
                                "findings": [], 
                                "created_at": time.time()
                            })
                            send_whatsapp_message(sender_phone, f"✅ Cloud Agent Deployed!\nTask ID: {scout_id}\n\nYou will receive an alert here as soon as a match is found on the web.")
                else:
                    selected_id = raw_id.replace("select_", "")
                    record = await tasks_collection.find_one({"yutori_task_id": selected_id})
                    if record:
                        finding_count = len(record.get("findings", []))
                        send_action_buttons(sender_phone, selected_id, record["query_instruction"], finding_count)

            elif inter_type == "button_reply":
                button_id = interactive_obj["button_reply"]["id"]

                # Onboarding Handlers
                if button_id == "onboard_upload":
                    await users_collection.update_one({"whatsapp_phone": sender_phone}, {"$set": {"onboarding_state": "AWAITING_RESUME_UPLOAD"}})
                    send_whatsapp_message(sender_phone, "Great! Please upload your resume PDF document now.")
                    return {"status": "success"}
                elif button_id == "onboard_skip":
                    await users_collection.update_one({"whatsapp_phone": sender_phone}, {"$set": {"onboarding_state": "COMPLETED"}})
                    send_whatsapp_message(sender_phone, "Setup complete! You can now ask me to track anything (e.g. 'Track flight prices from BLR to DEL').\n\n_Note: You can still upload a resume PDF at any time later to enable career scoring!_")
                    return {"status": "success"}

                # Task Control Handlers
                elif button_id.startswith("status_"):
                    target_id = button_id.replace("status_", "")
                    record = await tasks_collection.find_one({"yutori_task_id": target_id})
                    if record:
                        findings_list = record.get("findings", [])
                        if not findings_list:
                            send_whatsapp_message(sender_phone, "This scout is active but hasn't detected any matches yet.")
                        else:
                            summary = "*Recent Findings:*\n\n"
                            for entry_item in findings_list[-5:]:
                                summary += f"- {entry_item.get('text', str(entry_item))}\n"
                            summary += f"\n_{len(findings_list)} total finding(s)._"
                            send_whatsapp_message(sender_phone, summary)
                elif button_id.startswith("stop_"):
                    target_id = button_id.replace("stop_", "")
                    if terminate_yutori_scout(target_id):
                        await tasks_collection.update_one({"yutori_task_id": target_id}, {"$set": {"status": "stopped", "stopped_at": time.time()}})
                        send_whatsapp_message(sender_phone, f"[TERMINATED] Scout shut down.\nTask ID: {target_id}")
                    else:
                        send_whatsapp_message(sender_phone, "Termination request failed at Yutori endpoint. Try again.")

                # Opportunity Copilot Handlers
                elif button_id.startswith("apply_"):
                    target_id = button_id.replace("apply_", "")
                    opp = await opportunities_collection.find_one({"_id": target_id})
                    url = opp.get("url", "No URL available.") if opp else "No URL available."
                    send_whatsapp_message(sender_phone, f"Here is the direct link:\n{url}")
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
                            welcome_msg = f"✅ *Career Profile Locked In!*\n\n*Skills Detected:* {', '.join(profile.get('skills', []))}\n\nType *help* anytime to see available commands.\n\nNow, let's deploy your first agent!"
                            send_whatsapp_message(sender_phone, welcome_msg)
                            send_onboarding_choices(sender_phone)
                        else:
                            send_whatsapp_message(sender_phone, "Failed to extract skills from the PDF. Please try a different format.")
            return {"status": "success"}

        # Handle remaining onboarding strict states (if they send text instead of PDF)
        if user_state == "AWAITING_RESUME_DECISION":
            text_val = message_obj.get("text", {}).get("body", "").strip().lower() if msg_type == "text" else ""
            if text_val in ["skip", "cancel"]:
                await users_collection.update_one({"whatsapp_phone": sender_phone}, {"$set": {"onboarding_state": "COMPLETED"}})
                send_whatsapp_message(sender_phone, "Setup complete! You can now ask me to track anything (e.g. 'Track flight prices from BLR to DEL').\n\n_Note: You can still upload a resume PDF at any time later to enable career scoring!_")
            else:
                _post_whatsapp({
                    "messaging_product": "whatsapp",
                    "recipient_type": "individual",
                    "to": sender_phone,
                    "type": "interactive",
                    "interactive": {
                        "type": "button",
                        "body": {"text": "[HELLO] *Welcome to Nexus Universal Agent!*\n\nI can track *anything* on the internet for you—from flight prices and IPL scores to internships and hackathons.\n\n*Would you like to enable personalized career matching?* Uploading a resume allows me to analyze and score jobs/internships against your skills."},
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

                rag_prompt = f"You are Nexus AI Assistant. Answer the user's question. First, use this scraped context:\n{opp.get('raw_content', '')}\nIf the answer is NOT in the context, seamlessly search Google to find the answer.\nQuestion: {target_text}"
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

        # =============================================================
        #  FAST INTERCEPTS
        # =============================================================
        if target_text.lower() in HELP_KEYWORDS:
            send_whatsapp_message(sender_phone, COMMAND_MENU)
            return {"status": "success"}

        if target_text.upper().startswith("STOP "):
            target_id = target_text.split(maxsplit=1)[1].strip()
            record = await tasks_collection.find_one({"whatsapp_number": sender_phone, "yutori_task_id": target_id, "status": "active"})
            if record:
                if terminate_yutori_scout(target_id):
                    await tasks_collection.update_one({"yutori_task_id": target_id}, {"$set": {"status": "stopped", "stopped_at": time.time()}})
                    send_whatsapp_message(sender_phone, f"[TERMINATED] Scout shut down.\nTask ID: {target_id}")
                else:
                    send_whatsapp_message(sender_phone, "Termination request failed at Yutori endpoint. Try again.")
            else:
                send_whatsapp_message(sender_phone, "No active scout found with that Task ID.")
            return {"status": "success"}

        if target_text.lower() in {"list", "status", "scouts", "my scouts", "active", "show scouts"}:
            active_scouts = await tasks_collection.find({"whatsapp_number": sender_phone, "status": "active"}).to_list(length=10)
            if not active_scouts:
                send_whatsapp_message(sender_phone, "No active scouts running under your profile.")
            else:
                send_scout_list_menu(sender_phone, active_scouts)
            return {"status": "success"}

        if target_text.lower() in {"saved", "my board", "board", "tracked", "bookmarks"}:
            saved_ids = user.get("saved_opportunities", [])
            if not saved_ids:
                send_whatsapp_message(sender_phone, "Your tracking board is empty! Click '[SAVE] Save to Board' on alerts to bookmark them.")
            else:
                saved_opps = await opportunities_collection.find({"_id": {"$in": saved_ids}}).to_list(length=15)
                msg = "*Your Tracking Board:*\n\n"
                for idx, opp in enumerate(saved_opps, 1):
                    msg += f"{idx}. *{opp.get('title', 'Unknown')}*\n_{opp.get('url', 'No Link')}_\n\n"
                send_whatsapp_message(sender_phone, msg)
            return {"status": "success"}

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

        elif intent == "START":
            refined_instruction = analysis.get("refined_instruction")
            task_category = analysis.get("category", "GENERAL")
            if not refined_instruction:
                send_whatsapp_message(sender_phone, "Could not build a valid search query. Be more specific about what to track.")
                return {"status": "success"}

            existing = await tasks_collection.find_one({"whatsapp_number": sender_phone, "query_instruction": refined_instruction, "status": "active"})
            if existing:
                send_whatsapp_message(sender_phone, f"You already have an active scout with a matching query.\nTask ID: {existing['yutori_task_id']}")
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
                send_whatsapp_message(sender_phone, f"[SUCCESS] Cloud Agent Deployed!\nTask ID: {scout_id}\nType: {task_category}")
            else:
                send_whatsapp_message(sender_phone, "Scout deployment failed. Check server logs.")

    except Exception as e:
        print(f"Webhook Error: {e}")
    return {"status": "success"}

# =====================================================================
#  TRANSPARENT SCORING YUTORI WEBHOOK 
# =====================================================================

@app.post("/yutori-webhook")
async def receive_yutori_findings(request: Request):
    data = await request.json()
    task_id = data.get("task_id")
    raw_findings = data.get("findings", "")

    task = await tasks_collection.find_one({"yutori_task_id": task_id, "status": "active"})
    if task:
        target_phone = task["whatsapp_number"]
        task_type = task.get("task_type", "GENERAL")
        
        user = await users_collection.find_one({"whatsapp_phone": target_phone})
        profile_context = user.get("profile_json", {}) if user else {}

        # The Transparent Evaluator Prompt
        parse_prompt = f"""
        Extract data from this web crawl result: {raw_findings}
        
        Task Type: {task_type}
        
        If Task Type is "CAREER":
        Evaluate Semantic Relevance Distance against this profile metrics framework: {profile_context}
        Assign an explicit float score between 0.0 and 1.0 representing 'semantic_relevance'.
        
        If Task Type is "GENERAL" or User Profile is empty:
        Set semantic_relevance to null and reasoning to null. Just summarize the finding (e.g., the flight price, the IPL score, the court availability).
        
        CRITICAL: Extract any application closing date or deadline and format it as ISO 8601 string (e.g., "2026-07-21T00:00:00Z"). If no explicit deadline is found, set deadline_iso to null.
        
        Return STRICT JSON format:
        {{
            "title": "Short Title of finding",
            "summary": "Clear summary of the update",
            "url": "https://link-if-available",
            "deadline_iso": "2026-12-31T23:59:59Z",
            "semantic_relevance": 0.8,
            "reasoning": "1-sentence explanation of score (or null)"
        }}
        """
        try:
            res = gemini_client.models.generate_content(
                model=GATEKEEPER_MODEL, 
                contents=parse_prompt, 
                config=types.GenerateContentConfig(response_mime_type="application/json")
            )
            parsed = json.loads(res.text.strip())
        except:
            parsed = {"title": "New Update", "summary": raw_findings[:100], "url": "N/A", "deadline_iso": None, "semantic_relevance": 0.5, "reasoning": "Fallback parsing configuration implemented."}

        # --- DETERMINISTIC MATRIX SCORING IMPLEMENTATION ---
        final_priority_score = 0.5
        reasoning_line = parsed.get("reasoning", "")
        
        if task_type == "CAREER":
            s_relevance = float(parsed.get("semantic_relevance") or 0.5)
            s_urgency = 0.5 # Default middle balance weighting
            
            deadline_str = parsed.get("deadline_iso")
            if deadline_str:
                try:
                    deadline_dt = datetime.fromisoformat(deadline_str.replace("Z", "+00:00"))
                    time_remaining = deadline_dt - datetime.now(timezone.utc)
                    days_left = max(0, time_remaining.days)
                    # Mapping urgency index mathematically: close deadlines equal maximum metrics caps
                    if days_left <= 2: s_urgency = 1.0
                    elif days_left <= 7: s_urgency = 0.8
                    elif days_left <= 30: s_urgency = 0.5
                    else: s_urgency = 0.2
                except: pass
                
            # Compute Final Priority Index: (0.6 * Relevance) + (0.4 * Urgency)
            final_priority_score = (0.6 * s_relevance) + (0.4 * s_urgency)
            reasoning_line = f"Rel: {int(s_relevance*100)}% | Urg: {int(s_urgency*100)}%. Reason: {parsed.get('reasoning')}"

        # Save for RAG questioning
        finding_id = str(uuid.uuid4())[:8]
        await opportunities_collection.insert_one({
            "_id": finding_id,
            "yutori_task_id": task_id,
            "raw_content": raw_findings,
            "title": parsed.get("title", "New Update"),
            "url": parsed.get("url", "N/A"),
            "deadline_iso": parsed.get("deadline_iso"),
            "created_at": time.time()
        })

        # Append to the task list for status commands
        finding_entry = {
            "finding_id": finding_id,
            "text": f"[{parsed.get('title')}] {parsed.get('summary')}",
            "received_at": datetime.now(timezone.utc).isoformat()
        }
        await tasks_collection.update_one(
            {"yutori_task_id": task_id},
            {"$push": {"findings": finding_entry}}
        )

        # Send transparent notification
        send_opportunity_card(
            target_phone, 
            finding_id, 
            parsed.get("title", "Update"), 
            parsed.get("summary", ""), 
            parsed.get("url", "N/A"), 
            task_type,
            final_priority_score,
            reasoning_line
        )

        # Optional: Deliver a voice note alert ONLY for high-priority career matches
        if task_type == "CAREER" and final_priority_score >= 0.7:
            voice_script = f"Hello! Your Nexus scout found a high-priority match: {parsed.get('title')}. Please check your chat for details."
            deliver_voice_alert(target_phone, voice_script)

    return {"status": "processed"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
