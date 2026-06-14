import asyncio
import traceback
from datetime import datetime, timezone, timedelta
from database import tracked_collection, opportunities_collection
from services.whatsapp_api import send_whatsapp_message

# =====================================================================
#  AUTONOMOUS DEADLINE REMINDER BACKGROUND LOOP
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
                    msg = f"[ALERT] *OppTrax Deadline Reminder!*\n\nYour tracked opportunity *{opp.get('title')}* is closing soon!\n\nDon't forget to apply:\n{opp.get('url')}"
                    send_whatsapp_message(phone, msg)
                    
                    # Mark as reminded so we don't spam them
                    await tracked_collection.update_one({"_id": track_item["_id"]}, {"$set": {"reminded": True}})
                    print(f"[SUCCESS] Reminder sent to {phone} for task {opp.get('title')}", flush=True)
                    
        except Exception as e:
            print(f"Reminder Loop Error: {e}", flush=True)
            
        await asyncio.sleep(3600) # Wait 1 hour before checking again

# =====================================================================
#  AUTONOMOUS YUTORI POLLING LOOP
# =====================================================================

async def yutori_polling_loop():
    """Runs in the background, polling Yutori for updates on active tasks.
    
    Architecture:
    - Runs every 3 minutes
    - For each active task, pulls the latest updates from Yutori's API
    - Feeds each update through the same pipeline as the webhook handler
    - Deduplication is handled inside process_yutori_payload (idempotent)
    - Errors on one task do NOT prevent processing of other tasks
    """
    print("[SYSTEM] Autonomous Yutori Polling Loop Started!", flush=True)
    
    # Delay first poll by 15 seconds to let the server fully boot
    await asyncio.sleep(15)
    
    from database import tasks_collection
    from services.yutori_client import fetch_yutori_updates
    from routers.yutori_webhook import process_yutori_payload

    while True:
        try:
            # Query all active tasks
            active_tasks = await tasks_collection.find({"status": "active"}).to_list(length=50)
            
            if active_tasks:
                print(f"[POLLER] Polling {len(active_tasks)} active task(s)...", flush=True)
            
            for task in active_tasks:
                task_id = task.get("yutori_task_id")
                if not task_id:
                    continue
                
                try:
                    # Fetch updates directly from Yutori
                    updates = fetch_yutori_updates(task_id)
                    if not updates:
                        continue
                    
                    print(f"[POLLER] Task {task_id[:8]}... returned {len(updates)} update(s)", flush=True)
                    
                    for update in updates:
                        try:
                            # Normalize: handle both dict and string content
                            content = update.get("content", "") if isinstance(update, dict) else str(update)
                            update_id = update.get("id") if isinstance(update, dict) else None
                            
                            # Construct a payload that process_yutori_payload expects
                            payload = {
                                "task_id": task_id,
                                "findings": content,
                                "id": update_id
                            }
                            # Push it through the existing processing pipeline
                            result = await process_yutori_payload(payload)
                            
                            if result.get("status") == "processed" and result.get("processed_count", 0) > 0:
                                print(f"[POLLER] New finding(s) delivered for task {task_id[:8]}...", flush=True)
                            
                            # Throttle processing to protect Gemini API rate limits (15 RPM free tier)
                            await asyncio.sleep(5)
                            
                        except Exception as update_err:
                            print(f"[POLLER] Error processing single update for task {task_id[:8]}...: {update_err}", flush=True)
                            traceback.print_exc()
                            continue  # Don't let one bad update kill the loop
                        
                except Exception as task_err:
                    print(f"[POLLER] Error fetching updates for task {task_id[:8]}...: {task_err}", flush=True)
                    traceback.print_exc()
                    continue  # Don't let one bad task kill the loop
                    
        except Exception as e:
            print(f"[POLLER] ❌ Critical Polling Loop Error: {e}", flush=True)
            traceback.print_exc()
            
        await asyncio.sleep(180)  # Check every 3 minutes
