import re

SUPPORTED_LANGUAGES = {"en", "hi", "both"}

_HINDI_SCRIPT_RE = re.compile(r"[\u0900-\u097F]")
_HINGLISH_WORDS = {
    "kya",
    "kaise",
    "kab",
    "kitna",
    "kitni",
    "kahan",
    "batao",
    "bataiye",
    "samjhao",
    "milega",
    "milegi",
    "chahiye",
    "karna",
    "hoga",
    "hai",
    "hain",
    "mujhe",
    "mera",
    "meri",
    "mere",
    "hindi",
    "stipend",
    "paise",
    "yogya",
    "patrata",
    "antim",
    "tithi",
    "khojo",
    "dhundo",
    "dhoondo",
    "bhejo",
}


def normalize_language(raw: str | None, default: str = "both") -> str:
    if not raw:
        return default
    cleaned = raw.strip().lower()
    if cleaned in {"en", "eng", "english", "angrezi", "अंग्रेजी", "अंग्रेज़ी"}:
        return "en"
    if cleaned in {"hi", "hin", "hindi", "hinglish", "हिंदी", "हिन्दी"}:
        return "hi"
    if cleaned in {"both", "bilingual", "en+hi", "hi+en", "all", "दोनों", "dono"}:
        return "both"
    return default


def detect_query_language(text: str | None, preferred_language: str = "both") -> str:
    """Determines whether an answer should be in English ('en'), Hindi ('hi'), or both ('both')."""
    pref = normalize_language(preferred_language, default="both")
    if not text:
        return pref

    lowered = text.strip().lower()

    # Explicit language instruction inside the user message always takes precedence
    if any(
        phrase in lowered
        for phrase in [
            "in hindi",
            "hindi mein",
            "hindi me",
            "reply in hindi",
            "answer in hindi",
            "explain in hindi",
            "हिंदी में",
            "हिन्दी में",
        ]
    ):
        return "hi"
    if any(
        phrase in lowered
        for phrase in [
            "in english",
            "english mein",
            "english me",
            "reply in english",
            "answer in english",
            "explain in english",
            "अंग्रेजी में",
        ]
    ):
        return "en"
    if any(phrase in lowered for phrase in ["both languages", "hindi and english", "english and hindi", "दोनों भाषाओं"]):
        return "both"

    # Script / Hinglish detection for natural conversation
    if _HINDI_SCRIPT_RE.search(text):
        return "hi"
    tokens = set(re.findall(r"[a-z]+", lowered))
    if len(tokens & _HINGLISH_WORDS) >= 2:
        return "hi"

    return pref


def language_label(lang: str) -> str:
    norm = normalize_language(lang)
    if norm == "hi":
        return "हिंदी (Hindi)"
    if norm == "en":
        return "English"
    return "English + हिंदी (Both)"


def language_instruction_for_prompt(target_lang: str) -> str:
    norm = normalize_language(target_lang)
    if norm == "hi":
        return (
            "Respond strictly in clear, natural Hindi using Devanagari script (हिंदी). "
            "Keep technical nouns, company names, and URLs accurate."
        )
    if norm == "en":
        return "Respond strictly in clear, professional English."
    return (
        "Respond clearly in BOTH English and Hindi (Devanagari script): "
        "provide an *English* section followed by a *हिंदी (Hindi)* section."
    )


def format_bilingual_text(en_text: str, hi_text: str, lang: str = "both") -> str:
    norm = normalize_language(lang)
    if norm == "en":
        return en_text
    if norm == "hi":
        return hi_text
    return f"{en_text}\n\n---\n\n{hi_text}"
