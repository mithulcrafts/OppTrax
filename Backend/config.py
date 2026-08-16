import os
from dotenv import load_dotenv
from sarvamai import SarvamAI
from google import genai

load_dotenv(override=True)

# API Keys & Auth
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN")
NGROK_BASE_URL = os.getenv("NGROK_BASE_URL")
WHATSAPP_OTP_TEMPLATE_NAME = os.getenv("WHATSAPP_OTP_TEMPLATE_NAME", "")

SARVAM_KEY = os.getenv("SARVAM_KEY")
GEMINI_KEY = os.getenv("GEMINI_KEY")
YUTORI_KEY = os.getenv("YUTORI_KEY")
MONGO_URI = os.getenv("MONGO_URI")

# Clients
sarvam_client = SarvamAI(api_subscription_key=SARVAM_KEY) if SARVAM_KEY else None
gemini_client = genai.Client(api_key=GEMINI_KEY) if GEMINI_KEY else None

# Constants
GATEKEEPER_MODEL = "gemini-2.5-flash"

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
- "Track free tech conferences and meetups in Bengaluru."
- "Track international scholarships for computer science students."
"""
