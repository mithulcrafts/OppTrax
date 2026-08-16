import os
from dotenv import load_dotenv
from sarvamai import SarvamAI
from google import genai
import tenacity
from google.genai.errors import APIError
import asyncio

gemini_semaphore = asyncio.Semaphore(3)

load_dotenv(override=True)

# API Keys & Auth
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN")
NGROK_BASE_URL = os.getenv("NGROK_BASE_URL")

SARVAM_KEY = os.getenv("SARVAM_KEY")
GEMINI_KEY = os.getenv("GEMINI_KEY")
YUTORI_KEY = os.getenv("YUTORI_KEY")
MONGO_URI = os.getenv("MONGO_URI")

# Clients
sarvam_client = SarvamAI(api_subscription_key=SARVAM_KEY) if SARVAM_KEY else None
gemini_client = genai.Client(api_key=GEMINI_KEY) if GEMINI_KEY else None

def log_retry(retry_state):
    print(f"[RETRY] Gemini API call failed. Retrying... Attempt {retry_state.attempt_number}. Error: {retry_state.outcome.exception()}", flush=True)

def is_retryable_error(exception):
    # Immediately fail and drop the task if the daily quota is dead
    if "check your plan and billing details" in str(exception).lower():
        print("[WORKER] Daily quota exhausted. Dropping task.", flush=True)
        return False
    # Otherwise, it's a transient error or RPM spike—safe to retry
    return isinstance(exception, APIError)

@tenacity.retry(
    wait=tenacity.wait_exponential(multiplier=5, min=5, max=65),
    stop=tenacity.stop_after_attempt(5),
    retry=tenacity.retry_if_exception(is_retryable_error),
    before_sleep=log_retry,
    reraise=True
)
async def generate_content_with_retry(*args, **kwargs):
    if not gemini_client:
        raise ValueError("Gemini client is not initialized")
    async with gemini_semaphore:
        return await gemini_client.aio.models.generate_content(*args, **kwargs)

# Constants
GATEKEEPER_MODEL = "gemini-3.1-flash-lite"

MIN_INPUT_LENGTH = 3
MAX_INPUT_LENGTH = 500
GIBBERISH_PATTERNS = {"asdf", "qwer", "zxcv", "jkl;", "1234", "test", "aaa", "bbb", "xxx", "zzz"}

HELP_KEYWORDS = {"help", "menu", "commands", "options", "what can you do", "how to use"}

COMMAND_MENU = """
*OppTrax Command Center*

Here is what you can tell me to do:
- *list* - View all active scouts.
- *stop <Task ID>* - Terminate a specific scout.
- *help* - Show this menu.

*Examples of what you can ask me to track:*

- "Notify me as soon as a new AI startup funding seed round gets announced."

- "Track new VC seed rounds announced in SF."

- "Notify me when new projects get added to European Summer of Code with their summary."

- "Track free tech conferences and meetups for students in Bengaluru."

- "Track international scholarships for computer science students."
"""
