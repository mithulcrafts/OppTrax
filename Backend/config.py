import asyncio
import os
from pathlib import Path

import tenacity
from dotenv import load_dotenv
from google import genai
from google.genai.errors import APIError
from sarvamai import SarvamAI

BACKEND_DIR = Path(__file__).resolve().parent
ROOT_DIR = BACKEND_DIR.parent

# Optional custom env file override via AVSAR_ENV_FILE, then Backend/.env, then repo root .env
_custom_env = os.getenv("AVSAR_ENV_FILE", "").strip()
if _custom_env:
    _custom_path = Path(_custom_env)
    if not _custom_path.is_absolute():
        _custom_path = (ROOT_DIR / _custom_path).resolve()
    if _custom_path.exists():
        load_dotenv(_custom_path, override=False)

if (BACKEND_DIR / ".env").exists():
    load_dotenv(BACKEND_DIR / ".env", override=False)
if (ROOT_DIR / ".env").exists():
    load_dotenv(ROOT_DIR / ".env", override=False)

gemini_semaphore = asyncio.Semaphore(3)


def _env_str(name: str, default: str = "") -> str:
    val = os.getenv(name)
    return val.strip() if val is not None and val.strip() != "" else default


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env_str(name, str(default)))
    except ValueError:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = _env_str(name, "")
    if not raw:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


# Database Configuration
MONGO_URI = _env_str("MONGO_URI") or None
DATABASE_BACKEND = _env_str("DATABASE_BACKEND", "auto").lower()
if DATABASE_BACKEND not in {"auto", "sqlite", "mongodb"}:
    DATABASE_BACKEND = "auto"
USE_LOCAL_DB = _env_bool("USE_LOCAL_DB", default=False) or DATABASE_BACKEND == "sqlite"
LOCAL_DB_FALLBACK = _env_bool("LOCAL_DB_FALLBACK", default=True)
LOCAL_DB_PATH = _env_str("LOCAL_DB_PATH", str(BACKEND_DIR / "data" / "opptrax.db"))

# API Keys & Auth
WHATSAPP_APP_ID = _env_str("WHATSAPP_APP_ID") or None
WHATSAPP_WABA_ID = _env_str("WHATSAPP_WABA_ID") or None
WHATSAPP_TOKEN = _env_str("WHATSAPP_TOKEN") or None
PHONE_NUMBER_ID = _env_str("PHONE_NUMBER_ID") or None
VERIFY_TOKEN = _env_str("VERIFY_TOKEN") or _env_str("WHATSAPP_VERIFY_TOKEN", "opptrax_verify_token")
WHATSAPP_VERIFY_TOKEN = VERIFY_TOKEN
WHATSAPP_APP_SECRET = _env_str("WHATSAPP_APP_SECRET") or None
WHATSAPP_BUSINESS_NUMBER = _env_str("WHATSAPP_BUSINESS_NUMBER") or None
WHATSAPP_BOT_NUMBER = _env_str("WHATSAPP_BOT_NUMBER") or WHATSAPP_BUSINESS_NUMBER or None
GRAPH_API_VERSION = _env_str("GRAPH_API_VERSION") or _env_str("WHATSAPP_API_VERSION", "v23.0")
WHATSAPP_API_VERSION = GRAPH_API_VERSION
WHATSAPP_OTP_TEMPLATE_NAME = _env_str("WHATSAPP_OTP_TEMPLATE_NAME")
WHATSAPP_TEMPLATE_LANGUAGE = _env_str("WHATSAPP_TEMPLATE_LANGUAGE", "en_US")
WHATSAPP_REMINDER_TEMPLATE_NAME = _env_str("WHATSAPP_REMINDER_TEMPLATE_NAME")
WHATSAPP_ALERT_TEMPLATE_HI = _env_str("WHATSAPP_ALERT_TEMPLATE_HI")
WHATSAPP_ALERT_TEMPLATE_EN = _env_str("WHATSAPP_ALERT_TEMPLATE_EN")
WHATSAPP_ALERT_TEMPLATE_BOTH = _env_str("WHATSAPP_ALERT_TEMPLATE_BOTH")

# Optional Twilio WhatsApp fallback
TWILIO_ACCOUNT_SID = _env_str("TWILIO_ACCOUNT_SID") or None
TWILIO_AUTH_TOKEN = _env_str("TWILIO_AUTH_TOKEN") or None
TWILIO_WHATSAPP_NUMBER = _env_str("TWILIO_WHATSAPP_NUMBER") or None

NGROK_BASE_URL = _env_str("PUBLIC_BASE_URL") or _env_str("NGROK_BASE_URL") or None
SARVAM_KEY = _env_str("SARVAM_KEY") or _env_str("SARVAM_API_KEY") or None
SARVAM_CHAT_MODEL = _env_str("SARVAM_CHAT_MODEL", "sarvam-105b-conversations")
GEMINI_KEY = _env_str("GEMINI_KEY") or _env_str("GEMINI_API_KEY") or None
SCOUT_PROVIDER = _env_str("SCOUT_PROVIDER", "livescout").lower()
if SCOUT_PROVIDER not in {"livescout", "yutori", "hybrid"}:
    SCOUT_PROVIDER = "livescout"
ENABLE_YUTORI = _env_bool("ENABLE_YUTORI", default=False)
YUTORI_KEY = _env_str("YUTORI_KEY") or _env_str("YUTORI_API_KEY") or None
YUTORI_BASE_URL = _env_str("YUTORI_BASE_URL", "https://api.yutori.com/v1").rstrip("/")
YUTORI_WEBHOOK_SECRET = _env_str("YUTORI_WEBHOOK_SECRET") or None
YUTORI_WEBHOOK_BASE_URL = _env_str("YUTORI_WEBHOOK_BASE_URL") or None

AUTH_SECRET = _env_str("AUTH_SECRET", "opptrax-dev-session-secret-change-in-prod")
CORS_ORIGINS = [
    origin.strip()
    for origin in _env_str("CORS_ORIGINS", "*").split(",")
    if origin.strip()
]
SERVE_FRONTEND = _env_bool("SERVE_FRONTEND", default=True)
ENABLE_BACKGROUND_TASKS = _env_bool("ENABLE_BACKGROUND_TASKS", default=True)

# Scheduling & Polling Intervals
REMINDER_CHECK_INTERVAL_SECONDS = max(30, _env_int("REMINDER_CHECK_INTERVAL_SECONDS", 300))
YUTORI_POLL_INTERVAL_SECONDS = max(30, _env_int("YUTORI_POLL_INTERVAL_SECONDS", 180))
DAILY_DIGEST_HOUR_UTC = _env_int("DAILY_DIGEST_HOUR_UTC", 3)

# Clients
sarvam_client = SarvamAI(api_subscription_key=SARVAM_KEY) if SARVAM_KEY else None
gemini_client = genai.Client(api_key=GEMINI_KEY) if GEMINI_KEY else None

# Constants
GATEKEEPER_MODEL = _env_str("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_FALLBACK_MODELS = [
    m.strip()
    for m in _env_str(
        "GEMINI_FALLBACK_MODELS",
        "gemini-2.5-flash,gemini-2.5-flash-lite,gemini-2.0-flash",
    ).split(",")
    if m.strip()
]

MIN_INPUT_LENGTH = 2
MAX_INPUT_LENGTH = 1000
GIBBERISH_PATTERNS = {
    "asdf",
    "qwer",
    "zxcv",
    "jkl;",
    "1234",
    "aaa",
    "bbb",
    "xxx",
    "zzz",
}

HELP_KEYWORDS = {
    "help",
    "menu",
    "commands",
    "options",
    "what can you do",
    "how to use",
    "madad",
    "sahayata",
    "मदद",
    "मेनू",
    "सहायता",
}

COMMAND_MENU = """*OppTrax Command Center / ऑपट्रैक्स कमांड सेंटर*

*Commands / कमांड:*
- *search <query>* — Instant personalized opportunity search / तुरंत अवसर खोजें
- *list* — View active LiveScout monitors / सक्रिय स्काउट्स देखें
- *saved* / *board* — View your Tracking Board / ट्रैक किए गए अवसर देखें
- *details <id>* — Full bilingual details / पूरी जानकारी (English + हिंदी)
- *lang en | hi | both* — Change language / भाषा बदलें
- *digest* — Get Top-5 curated digest / आज के टॉप अवसर पाएं
- *profile* — View or update your profile / प्रोफ़ाइल देखें
- *stop <Task ID>* — Stop an active scout / स्काउट बंद करें
- *help* — Show this menu / यह मेनू देखें

*Examples / उदाहरण:*
- "Find AI internships in Bengaluru"
- "मेरे लिए रिमोट पायथन इंटर्नशिप खोजो"
- "Track international CS scholarships with deadlines"
- Send a poster image, URL, or PDF resume anytime!"""


USER_TRACKING_CHOICES = {
    "onboard_manual": (
        "*Manual Mode Selected!*\n"
        "You can type or send a voice note anytime (English or हिंदी), "
        "or reply *help* to see all commands."
    ),
}

TRACKING_INTENTS = {
    "onboard_internships": "Find currently open software, AI, and engineering internships matching candidate skills.",
    "onboard_jobs": "Find open entry-level and junior full-time engineering and AI roles.",
    "onboard_hackathons": "Find upcoming online and hybrid coding hackathons with prize pools and deadlines.",
    "onboard_conferences": "Monitor major developer conferences, AI summits, and CFP deadlines.",
    "onboard_scholarships": "Track international and national tech scholarships, fellowships, and grants.",
    "onboard_flights": "Track cheapest BLR to DEL flight deals under 5000 INR.",
    "onboard_vc_sf": "Monitor newly announced seed and pre-seed VC funding rounds in San Francisco.",
    "onboard_esoc": "Monitor new open-source project listings in European Summer of Code (ESoC).",
    "onboard_blr_events": "Monitor free developer meetups and tech events in Bengaluru.",
}


def _normalize_http_url(raw_url: str | None) -> str | None:
    if not raw_url:
        return None
    cleaned = raw_url.strip().rstrip("/")
    if cleaned.startswith("http://") or cleaned.startswith("https://"):
        return cleaned
    return None


def get_public_base_url() -> str | None:
    return _normalize_http_url(NGROK_BASE_URL)


def get_yutori_webhook_base_url() -> str | None:
    return _normalize_http_url(YUTORI_WEBHOOK_BASE_URL)


def integration_status() -> dict:
    return {
        "database_backend": DATABASE_BACKEND,
        "whatsapp_meta": bool(WHATSAPP_TOKEN and PHONE_NUMBER_ID),
        "whatsapp_twilio": bool(TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_WHATSAPP_NUMBER),
        "whatsapp_app_configured": bool(WHATSAPP_APP_ID and WHATSAPP_APP_SECRET),
        "whatsapp_waba_configured": bool(WHATSAPP_WABA_ID),
        "whatsapp_business_number": WHATSAPP_BUSINESS_NUMBER,
        "graph_api_version": GRAPH_API_VERSION,
        "whatsapp_templates": {
            "otp": bool(WHATSAPP_OTP_TEMPLATE_NAME),
            "reminder": bool(WHATSAPP_REMINDER_TEMPLATE_NAME),
            "alert_hi": bool(WHATSAPP_ALERT_TEMPLATE_HI),
            "alert_en": bool(WHATSAPP_ALERT_TEMPLATE_EN),
            "alert_both": bool(WHATSAPP_ALERT_TEMPLATE_BOTH),
        },
        "scout_provider": SCOUT_PROVIDER,
        "scout_engine": "LiveScout Multi-Source (Unstop + Devpost + Remotive + Arbeitnow + RSS)",
        "live_scout": True,
        "yutori": bool(ENABLE_YUTORI and YUTORI_KEY),
        "yutori_webhook_configured": bool(get_yutori_webhook_base_url()),
        "yutori_webhook_secret": bool(YUTORI_WEBHOOK_SECRET),
        "gemini": bool(gemini_client),
        "sarvam": bool(SARVAM_KEY),
        "sarvam_llm": bool(sarvam_client),
        "public_webhook_url": get_public_base_url(),
        "background_tasks_enabled": ENABLE_BACKGROUND_TASKS,
    }


def _call_sarvam_chat_sync(
    messages: list[dict[str, str]],
    max_tokens: int = 1200,
    temperature: float = 0.2,
) -> str | None:
    if not sarvam_client:
        return None
    res = sarvam_client.chat.completions(
        model=SARVAM_CHAT_MODEL,
        messages=messages,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    choices = getattr(res, "choices", None) or []
    if not choices:
        return None
    msg = getattr(choices[0], "message", None)
    text = getattr(msg, "content", None)
    return text.strip() if isinstance(text, str) and text.strip() else None


async def generate_sarvam_chat(
    messages: list[dict[str, str]],
    max_tokens: int = 1200,
    temperature: float = 0.2,
) -> str | None:
    """Async wrapper for Sarvam conversational chat completion."""
    if not sarvam_client:
        return None
    return await asyncio.to_thread(_call_sarvam_chat_sync, messages, max_tokens, temperature)


def _translate_hi_sync(text: str) -> str | None:
    if not sarvam_client or not text or not text.strip():
        return None
    res = sarvam_client.text.translate(
        input=text.strip()[:900],
        source_language_code="en-IN",
        target_language_code="hi-IN",
    )
    translated = getattr(res, "translated_text", None)
    return translated.strip() if isinstance(translated, str) and translated.strip() else None


async def translate_to_hindi_sarvam(text: str) -> str | None:
    """Translates concise English opportunity copy to natural Hindi via Sarvam AI."""
    if not sarvam_client or not text or not text.strip():
        return None
    try:
        return await asyncio.to_thread(_translate_hi_sync, text)
    except Exception as exc:
        print(f"[SARVAM TRANSLATE] Fallback skipped translation: {exc}", flush=True)
        return None


def log_retry(retry_state):
    print(
        f"[RETRY] Gemini API call failed. Attempt {retry_state.attempt_number}. "
        f"Error: {retry_state.outcome.exception()}",
        flush=True,
    )


_NON_RETRYABLE_GEMINI_MARKERS = (
    "401",
    "403",
    "400",
    "404",
    "unauthenticated",
    "permission_denied",
    "api key not valid",
    "api_key_invalid",
    "invalid_argument",
    "not found",
    "not supported",
)


def is_retryable_error(exception):
    msg = str(exception).lower()
    if "check your plan and billing details" in msg or ("quota" in msg and "exceeded" in msg):
        print("[WORKER] Gemini quota exhausted. Skipping retry.", flush=True)
        return False
    if any(marker in msg for marker in _NON_RETRYABLE_GEMINI_MARKERS):
        return False
    return isinstance(exception, APIError)


@tenacity.retry(
    wait=tenacity.wait_exponential(multiplier=2, min=2, max=20),
    stop=tenacity.stop_after_attempt(3),
    retry=tenacity.retry_if_exception(is_retryable_error),
    before_sleep=log_retry,
    reraise=True,
)
async def _call_gemini_once(model: str, contents, config=None):
    if not gemini_client:
        raise ValueError("Gemini client is not initialized")
    kwargs = {"model": model, "contents": contents}
    if config is not None:
        kwargs["config"] = config
    async with gemini_semaphore:
        return await gemini_client.aio.models.generate_content(**kwargs)


async def generate_content_with_retry(*args, **kwargs):
    if not gemini_client:
        raise ValueError("Gemini client is not initialized")

    requested_model = kwargs.get("model") or GATEKEEPER_MODEL
    contents = kwargs.get("contents") if "contents" in kwargs else (args[0] if args else "")
    config = kwargs.get("config")

    candidates = [requested_model]
    for fallback in GEMINI_FALLBACK_MODELS:
        if fallback not in candidates:
            candidates.append(fallback)

    last_error = None
    for candidate_model in candidates:
        try:
            return await _call_gemini_once(candidate_model, contents, config=config)
        except Exception as err:
            last_error = err
            err_msg = str(err).lower()
            if "not found" in err_msg or "404" in err_msg or "not supported" in err_msg:
                print(
                    f"[GEMINI] Model '{candidate_model}' unavailable ({err}). Trying fallback...",
                    flush=True,
                )
                continue
            raise
    raise last_error
