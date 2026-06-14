import requests
import os
import time
import base64
from datetime import datetime
import re
import html
from config import WHATSAPP_TOKEN, PHONE_NUMBER_ID, SARVAM_KEY

def _post_whatsapp(data: dict):
    url = f"https://graph.facebook.com/v20.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    try:
        res = requests.post(url, headers=headers, json=data, timeout=10)
        if res.status_code >= 400:
            print(f"WhatsApp API Error {res.status_code}: {res.text}", flush=True)
    except Exception as e:
        print(f"WhatsApp send error: {e}", flush=True)

def format_html_for_whatsapp(raw_html: str) -> str:
    if not raw_html: return ""
    text = html.unescape(raw_html)
    # Convert headings to bold
    text = re.sub(r'<h[1-6]>(.*?)</h[1-6]>', r'*\1*\n', text, flags=re.IGNORECASE)
    # Convert paragraphs
    text = re.sub(r'<p>(.*?)</p>', r'\1\n', text, flags=re.IGNORECASE|re.DOTALL)
    # Bold and strong
    text = re.sub(r'<b>(.*?)</b>', r'*\1*', text, flags=re.IGNORECASE|re.DOTALL)
    text = re.sub(r'<strong>(.*?)</strong>', r'*\1*', text, flags=re.IGNORECASE|re.DOTALL)
    # Italics
    text = re.sub(r'<i>(.*?)</i>', r'_\1_', text, flags=re.IGNORECASE|re.DOTALL)
    text = re.sub(r'<em>(.*?)</em>', r'_\1_', text, flags=re.IGNORECASE|re.DOTALL)
    # Lists
    text = re.sub(r'<li>(.*?)</li>', r'• \1\n', text, flags=re.IGNORECASE|re.DOTALL)
    # Line breaks
    text = re.sub(r'<br\s*/?>', r'\n', text, flags=re.IGNORECASE)
    # Links
    text = re.sub(r'<a\s+[^>]*href=["\'](.*?)["\'][^>]*>(.*?)</a>', r'\2 (\1)', text, flags=re.IGNORECASE)
    # Strip remaining HTML tags
    text = re.sub(r'<[^>]+>', '', text)
    # Clean up excessive newlines
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

def send_whatsapp_message(to_phone: str, text_content: str):
    chunk_size = 4000
    for i in range(0, len(text_content), chunk_size):
        chunk = text_content[i:i + chunk_size]
        _post_whatsapp({
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to_phone,
            "type": "text",
            "text": {"preview_url": False, "body": chunk}
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
            "header": {"type": "text", "text": "OppTrax Control Panel"},
            "body": {"text": "Select a scout from the list below to inspect findings or manage execution."},
            "footer": {"text": f"{len(active_tasks)} active scout(s)"},
            "action": {
                "button": "View Scouts",
                "sections": [{"title": "Active Scouts", "rows": rows}]
            }
        }
    })

def send_action_buttons(to_phone: str, task_id: str, instruction: str, finding_count: int, is_active: bool = True):
    buttons = [
        {"type": "reply", "reply": {"id": f"status_{task_id}", "title": "View Findings"}}
    ]
    if is_active:
        buttons.append({"type": "reply", "reply": {"id": f"stop_{task_id}", "title": "Stop Scout"}})

    _post_whatsapp({
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to_phone,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": f"*Scout Detail*\n\nQuery: _{instruction}_\nFindings collected: {finding_count}\n\nChoose an action:"},
            "action": {
                "buttons": buttons
            }
        }
    })

def send_opportunity_card(to_phone: str, finding_id: str, title: str, summary: str, url: str, task_type: str, priority_score: float = None, reasoning: str = None, deadline: str = None):
    """Generates the clean interactive UI card for showing findings to the user."""
    card_text = f"*New Opportunity Found!*\n\n"
    card_text += f"*Title:* {title}\n"
    card_text += f"*Summary:* {summary}\n"
    
    if deadline and str(deadline).strip().lower() not in ["null", "none", "n/a", ""]:
        # Try to format date nicely if it's in ISO format
        try:
            dt = datetime.fromisoformat(deadline.replace("Z", "+00:00"))
            formatted_date = dt.strftime("%B %d, %Y")
            card_text += f"*Deadline:* {formatted_date}\n"
        except:
            card_text += f"*Deadline:* {deadline}\n"
            
    card_text += f"\n*Link:* {url}"

    buttons = [
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
        {"id": "onboard_internships", "title": "Internships", "description": "Track internships matching my skills"},
        {"id": "onboard_jobs", "title": "Full-Time Roles", "description": "Monitor entry/mid level jobs"},
        {"id": "onboard_hackathons", "title": "Hackathons", "description": "Find open source & hackathons"},
        {"id": "onboard_conferences", "title": "Conferences", "description": "Find tech conferences & summits"},
        {"id": "onboard_scholarships", "title": "Scholarships", "description": "Track international tech scholarships"},
        {"id": "onboard_flights", "title": "BLR-DEL Flights", "description": "Track flight prices under 5000 INR"},
        {"id": "onboard_vc_sf", "title": "SF VC Seed Rounds", "description": "Track SF funding announcements"},
        {"id": "onboard_esoc", "title": "Euro Summer of Code", "description": "Track ESoC project additions"},
        {"id": "onboard_blr_events", "title": "Bengaluru Events", "description": "Track free tech events in BLR"},
        {"id": "onboard_manual", "title": "Manual Mode Only", "description": "I will specify what to track manually"}
    ]
    _post_whatsapp({
        "messaging_product": "whatsapp", "recipient_type": "individual", "to": to_phone,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "header": {"type": "text", "text": "Select Your Tracking Mode"},
            "body": {"text": "I can automatically deploy a Scout based on your preferences. What would you like me to monitor?"},
            "footer": {"text": "You can change this anytime."},
            "action": {"button": "Choose an Option", "sections": [{"title": "Autonomous Deployment", "rows": rows}]}
        }
    })

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
