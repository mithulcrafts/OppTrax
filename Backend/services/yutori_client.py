import requests
from config import YUTORI_KEY, NGROK_BASE_URL

# =====================================================================
#  CLOUD WORKER ALLOCATION (Yutori API)
# =====================================================================

def launch_yutori_scout(instruction: str) -> str | None:
    print(f"[YUTORI] Dispatching cloud scout for: {instruction}", flush=True)
    url = "https://api.yutori.com/v1/scouting/tasks"
    headers = {"X-API-Key": YUTORI_KEY, "Content-Type": "application/json"}
    data = {
        "query": instruction,
        "output_interval": 3600,
        "webhook_url": f"{NGROK_BASE_URL}/yutori-webhook"
    }
    try:
        res = requests.post(url, json=data, headers=headers, timeout=12)
        if res.status_code in [200, 201]:
            return res.json().get("id")
    except Exception as e:
        print(f"[YUTORI ERROR] {e}", flush=True)
    return None

def terminate_yutori_scout(task_id: str) -> bool:
    print(f"[YUTORI] Sending termination signal for Task ID: {task_id}", flush=True)
    url = f"https://api.yutori.com/v1/scouting/tasks/{task_id}"
    headers = {"X-API-Key": YUTORI_KEY}
    try:
        res = requests.delete(url, headers=headers, timeout=10)
        if res.status_code not in [200, 202, 204]:
            print(f"[YUTORI STOP ERROR] HTTP {res.status_code}: {res.text}", flush=True)
            # If 403 or 404, the task is inaccessible or doesn't exist, so we consider it stopped locally
            if res.status_code in [403, 404]:
                return True
        return res.status_code in [200, 202, 204]
    except Exception as e:
        print(f"[YUTORI STOP ERROR] {e}", flush=True)
        return False

def fetch_yutori_updates(task_id: str) -> list:
    """Pulls missed findings directly from the Yutori API."""
    url = f"https://api.yutori.com/v1/scouting/tasks/{task_id}/updates?page_size=10"
    
    # Import YUTORI_KEY from your config.py
    from config import YUTORI_KEY 
    headers = {"X-API-Key": YUTORI_KEY} 
    
    try:
        res = requests.get(url, headers=headers)
        if res.status_code == 200:
            return res.json().get("updates", [])
    except Exception as e:
        print(f"Error fetching Yutori updates: {e}", flush=True)
    return []
