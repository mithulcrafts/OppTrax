import json
import hashlib
import time
import traceback
import asyncio
from datetime import datetime, timezone
from fastapi import APIRouter, Request, BackgroundTasks
from config import gemini_client, GATEKEEPER_MODEL
from database import tasks_collection, users_collection, opportunities_collection
from services.whatsapp_api import send_opportunity_card
from google.genai import types

router = APIRouter()

# =====================================================================
#  TRANSPARENT SCORING YUTORI WEBHOOK 
# =====================================================================

@router.post("/yutori-webhook")
async def receive_yutori_findings(request: Request, background_tasks: BackgroundTasks):
    data = await request.json()
    background_tasks.add_task(process_yutori_payload, data)
    return {"status": "success"}

async def process_yutori_payload(data: dict):
    """
    Processes a crawl payload containing findings. Can be called from:
      1. The /yutori-webhook endpoint (Yutori pushes data)
      2. The background polling loop (we pull data)
    
    Must be fully idempotent — safe to call multiple times with the same data.
    """
    try:
        task_id = data.get("task_id")
        raw_findings = data.get("findings", "")
        
        # Normalize raw_findings to always be a string
        if not isinstance(raw_findings, str):
            raw_findings = json.dumps(raw_findings, default=str)
        
        # Skip empty findings
        if not raw_findings or not raw_findings.strip():
            print(f"[POLLER] Skipping empty finding for task {task_id}", flush=True)
            return {"status": "skipped_empty"}
        
        task = await tasks_collection.find_one({"yutori_task_id": task_id, "status": "active"})
        if not task:
            return {"status": "no_active_task"}
            
        target_phone = task["whatsapp_number"]
        task_type = task.get("task_type", "GENERAL")
        
        user = await users_collection.find_one({"whatsapp_phone": target_phone})
        profile_context = user.get("profile_json", {}) if user else {}

        # ── GEMINI MULTI-FINDING PARSING ─────────────────────────────
        parse_prompt = f"""
        Extract ALL individual finding items or opportunities from this web crawl result:
        {raw_findings}
        
        Task Type: {task_type}
        
        For each individual opportunity/finding found:
        - Extract its title/role, URL, and deadline.
        - The `summary` MUST BE HIGHLY DETAILED. Do NOT send minimal information. Include ALL relevant information provided such as exact deadlines, dates of the event, location, stipends, and specific prerequisites.
        - CRITICAL RULE: DO NOT use any emojis whatsoever in your output. Your summary must be professional, clean, and purely textual.
        - CRITICAL RULE: DO NOT start with conversational filler like "Here is...", "I found...", "Based on...". Start the summary directly with the factual content.
        - The `title` MUST be the exact canonical name of the opportunity/event. Do NOT paraphrase or abbreviate it.
        - If Task Type is "CAREER" and user profile is not empty, evaluate semantic relevance (0.0 to 1.0) against this profile: {profile_context}
        - Otherwise (GENERAL or empty profile), set semantic_relevance to null and reasoning to null.
        - Extract any application closing date or deadline and format it as ISO 8601 string (e.g., "2026-07-21T00:00:00Z"). If no explicit deadline is found, set deadline_iso to null.
        
        Return a STRICT JSON list of objects:
        [
            {{
                "title": "Exact Canonical Title",
                "summary": "Highly detailed professional summary without emojis, including all dates, locations, stipends, and prerequisites. No conversational filler.",
                "url": "https://link-if-available",
                "deadline_iso": "2026-12-31T23:59:59Z",
                "semantic_relevance": 0.8,
                "reasoning": "1-sentence explanation of score (or null)"
            }},
            ...
        ]
        """
        try:
            res = gemini_client.models.generate_content(
                model=GATEKEEPER_MODEL, 
                contents=parse_prompt, 
                config=types.GenerateContentConfig(response_mime_type="application/json")
            )
            parsed_list = json.loads(res.text.strip())
            
            # Handle cases where Gemini returns a single JSON object instead of a list
            if isinstance(parsed_list, dict):
                parsed_list = [parsed_list]
            if not isinstance(parsed_list, list):
                raise ValueError("Parsed output is not a list")
        except Exception as gemini_err:
            print(f"[POLLER] Gemini parse failed for task {task_id[:8]}: {gemini_err}", flush=True)
            # Fallback to single finding summary
            parsed_list = [{
                "title": "New Update",
                "summary": raw_findings[:150],
                "url": "N/A",
                "deadline_iso": None,
                "semantic_relevance": 0.5,
                "reasoning": "Fallback parsing configuration implemented."
            }]

        # ── DEDUPLICATION: Check DB directly for each finding ─────────
        # We query the opportunities_collection directly instead of relying
        # on the in-memory task.findings array, which can be stale during
        # concurrent polling cycles.
        processed_count = 0

        for parsed in parsed_list:
            if not isinstance(parsed, dict):
                continue
                
            title_str = parsed.get("title", "New Update").strip()
            url_str = parsed.get("url", "N/A").strip()
            summary_str = parsed.get("summary", "").strip()
            
            # Generate deterministic ID: hash of task_id + normalized(title + url)
            # Normalize to lowercase to prevent Gemini casing differences from creating duplicates
            finding_id_src = f"{task_id}_{title_str.lower()}_{url_str.lower()}"
            finding_id = hashlib.md5(finding_id_src.encode("utf-8")).hexdigest()[:12]
            
            # Deduplication: check the opportunities collection directly (authoritative source)
            existing_opp = await opportunities_collection.find_one({"_id": finding_id})
            if existing_opp:
                print(f"[POLLER] Finding {finding_id} ({title_str}) is a duplicate, skipping.", flush=True)
                continue
                
            processed_count += 1

            # ── DETERMINISTIC MATRIX SCORING ─────────────────────────────
            final_priority_score = 0.5
            reasoning_line = parsed.get("reasoning", "")
            
            if task_type == "CAREER":
                s_relevance = float(parsed.get("semantic_relevance") or 0.5)
                s_urgency = 0.5

                deadline_str = parsed.get("deadline_iso")
                if deadline_str:
                    try:
                        deadline_dt = datetime.fromisoformat(deadline_str.replace("Z", "+00:00"))
                        time_remaining = deadline_dt - datetime.now(timezone.utc)
                        days_left = max(0, time_remaining.days)
                        if days_left <= 2: s_urgency = 1.0
                        elif days_left <= 7: s_urgency = 0.8
                        elif days_left <= 30: s_urgency = 0.5
                        else: s_urgency = 0.2
                    except: pass
                    
                final_priority_score = (0.6 * s_relevance) + (0.4 * s_urgency)
                reasoning_line = f"Rel: {int(s_relevance*100)}% | Urg: {int(s_urgency*100)}%. Reason: {parsed.get('reasoning')}"

            # Save opportunity to DB FIRST (acts as the dedup lock)
            await opportunities_collection.insert_one({
                "_id": finding_id,
                "yutori_task_id": task_id,
                "raw_content": raw_findings[:2000],
                "title": title_str,
                "url": url_str,
                "summary": summary_str,
                "deadline_iso": parsed.get("deadline_iso"),
                "created_at": time.time()
            })

            # Append to task findings array
            finding_entry = {
                "finding_id": finding_id,
                "text": f"[{title_str}] {summary_str}",
                "received_at": datetime.now(timezone.utc).isoformat()
            }
            await tasks_collection.update_one(
                {"yutori_task_id": task_id},
                {"$push": {"findings": finding_entry}}
            )

            # Send WhatsApp card
            print(f"[POLLER] Sending WhatsApp notification for {finding_id} ({title_str}) to {target_phone}", flush=True)
            send_opportunity_card(
                to_phone=target_phone, 
                finding_id=finding_id, 
                title=title_str, 
                summary=summary_str, 
                url=url_str, 
                task_type=task_type,
                priority_score=final_priority_score,
                reasoning=reasoning_line,
                deadline=parsed.get("deadline_iso")
            )

            # Throttle to protect Gemini API rate limits (15 RPM free tier)
            await asyncio.sleep(4)

        return {"status": "processed", "processed_count": processed_count}
    
    except Exception as e:
        print(f"[POLLER] CRITICAL ERROR processing finding: {e}", flush=True)
        traceback.print_exc()
        return {"status": "error"}
