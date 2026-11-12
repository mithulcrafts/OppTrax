import asyncio
import ipaddress
import json
import re
import socket
from typing import Any
from urllib.parse import urlparse

import requests
from google.genai import types

from config import (
    GATEKEEPER_MODEL,
    gemini_client,
    generate_content_with_retry,
    generate_sarvam_chat,
    translate_to_hindi_sarvam,
)
from services.language import (
    detect_query_language,
    language_instruction_for_prompt,
    normalize_language,
)
from services.live_scout import fetch_deep_opportunity_context, search_live_opportunities


def _strip_json_fences(raw_text: str) -> str:
    text = raw_text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    elif "```" in text:
        fenced = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
        if fenced:
            return fenced.group(1).strip()
    return text.strip()


def _safe_json_loads(raw_text: str) -> Any:
    cleaned = _strip_json_fences(raw_text)
    cleaned_no_trailing_commas = re.sub(r",\s*([}\]])", r"\1", cleaned)
    for candidate in (cleaned, cleaned_no_trailing_commas):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    decoder = json.JSONDecoder()
    starts = [idx for ch in ("{", "[") if (idx := cleaned.find(ch)) != -1]
    if starts:
        snippet = cleaned[min(starts):]
        for attempt in (snippet, re.sub(r",\s*([}\]])", r"\1", snippet)):
            try:
                parsed, _ = decoder.raw_decode(attempt)
                return parsed
            except json.JSONDecodeError:
                pass
    return json.loads(cleaned)


def _is_safe_public_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return False
        hostname = parsed.hostname
        if not hostname:
            return False
        if hostname.lower() in {"localhost", "0.0.0.0"} or hostname.lower().endswith(".local"):
            return False
        addr_infos = socket.getaddrinfo(hostname, None)
        for info in addr_infos:
            ip_str = info[4][0]
            ip_obj = ipaddress.ip_address(ip_str)
            if (
                ip_obj.is_private
                or ip_obj.is_loopback
                or ip_obj.is_link_local
                or ip_obj.is_multicast
                or ip_obj.is_reserved
            ):
                return False
        return True
    except Exception:
        return False


def _fetch_url_text_sync(url: str) -> str:
    if not _is_safe_public_url(url):
        return ""
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; OppTraxBot/1.0)"}
        res = requests.get(url, headers=headers, timeout=12, allow_redirects=True)
        if res.status_code >= 400:
            return ""
        text = re.sub(r"<script[^>]*>.*?</script>", " ", res.text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:12000]
    except Exception as exc:
        print(f"[URL FETCH] Could not fetch {url}: {exc}", flush=True)
        return ""


async def fetch_url_text(url: str) -> str:
    return await asyncio.to_thread(_fetch_url_text_sync, url)


def _personalization_context(
    user_profile: dict[str, Any] | None,
    learned_preferences: dict[str, Any] | None = None,
) -> str:
    parts = []
    if user_profile:
        skills = user_profile.get("skills") or []
        interests = user_profile.get("interests") or []
        exp_level = user_profile.get("experience_level") or ""
        locations = user_profile.get("preferred_locations") or []
        goals = user_profile.get("target_roles") or []
        parts.append(
            f"Skills: {skills}, Experience: {exp_level}, Interests: {interests}, "
            f"Goals: {goals}, Preferred Locations: {locations}, Summary: {user_profile.get('summary', '')}"
        )
    if learned_preferences:
        liked = learned_preferences.get("liked_keywords", [])[:15]
        disliked = learned_preferences.get("disliked_keywords", [])[:15]
        if liked or disliked:
            parts.append(f"Learned Likes: {liked}, Disliked/Avoid: {disliked}")
    return " | ".join(parts) if parts else "Anonymous candidate (no resume/profile uploaded yet)"


def _heuristic_intent_fallback(
    raw_text: str,
    user_profile: dict[str, Any] | None = None,
    learned_preferences: dict[str, Any] | None = None,
) -> dict[str, Any]:
    lowered = raw_text.strip().lower()
    if any(w in lowered for w in ["stop", "cancel scout", "band karo", "रोको", "बंद"]):
        return {"intent": "STOP", "refined_instruction": None, "category": "GENERAL", "reply": None}

    career_words = [
        "intern",
        "job",
        "hackathon",
        "fellowship",
        "scholarship",
        "grant",
        "career",
        "bounty",
        "opening",
        "role",
        "इंटर्नशिप",
        "नौकरी",
        "छात्रवृत्ति",
        "हैकाथॉन",
        "अवसर",
    ]
    is_career = any(w in lowered for w in career_words)
    skills = ", ".join((user_profile or {}).get("skills", [])[:8])
    liked = ", ".join((learned_preferences or {}).get("liked_keywords", [])[:5])
    profile_suffix = ""
    if skills:
        profile_suffix += f" Candidate skills: {skills}."
    if liked:
        profile_suffix += f" Preferred topics: {liked}."

    if is_career:
        return {
            "intent": "CAREER",
            "category": "CAREER",
            "refined_instruction": (
                f"Find currently open and newly announced opportunities matching: {raw_text}.{profile_suffix} "
                "Extract exact title, organization, closing deadline, stipend/reward, "
                "eligibility, location, and official application URL."
            ),
            "reply": None,
        }
    return {
        "intent": "GENERAL",
        "category": "GENERAL",
        "refined_instruction": f"Monitor and extract verified details + official links for: {raw_text}.{profile_suffix}",
        "reply": None,
    }


async def _try_gemini_text(
    prompt: str,
    use_google_search: bool = False,
    json_mode: bool = False,
) -> tuple[str | None, bool]:
    if not gemini_client:
        return None, False
    cfg = None
    if use_google_search:
        cfg = types.GenerateContentConfig(tools=[{"google_search": {}}])
    elif json_mode:
        cfg = types.GenerateContentConfig(response_mime_type="application/json")
    try:
        res = await generate_content_with_retry(model=GATEKEEPER_MODEL, contents=prompt, config=cfg)
        return (res.text or "").strip() or None, use_google_search
    except Exception as exc:
        print(f"[GEMINI] Call failed ({exc}); checking fallback.", flush=True)
        if use_google_search and not _is_auth_or_quota_error(exc):
            try:
                res = await generate_content_with_retry(model=GATEKEEPER_MODEL, contents=prompt)
                return (res.text or "").strip() or None, False
            except Exception as inner_exc:
                print(f"[GEMINI] Plain fallback failed: {inner_exc}", flush=True)
        return None, False


async def _ai_generate_text(
    prompt: str,
    use_google_search: bool = False,
    json_mode: bool = False,
    max_tokens: int = 1300,
) -> tuple[str | None, bool]:
    text, grounded = await _try_gemini_text(prompt, use_google_search=use_google_search, json_mode=json_mode)
    if text:
        return text, grounded
    try:
        sarvam_text = await generate_sarvam_chat(
            [{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
        )
        if sarvam_text:
            return sarvam_text, False
    except Exception as exc:
        print(f"[SARVAM LLM] Chat fallback failed: {exc}", flush=True)
    return None, False


async def analyze_intent(
    raw_text: str,
    user_profile: dict[str, Any] | None = None,
    learned_preferences: dict[str, Any] | None = None,
) -> dict[str, Any]:
    print(f"[GATEKEEPER] Analyzing raw intent: {raw_text[:60]}...", flush=True)
    profile_ctx = _personalization_context(user_profile, learned_preferences)
    prompt = f"""You are the Dual-Routing Intent Center for OppTrax, a multilingual (EN + HI) opportunity copilot.
The user texted: "{raw_text}"
User Profile & Learned Preferences: {profile_ctx}

Strictly categorize this input into ONE operational track:
1. "CAREER": Discover/search jobs, internships, hackathons, fellowships, scholarships, or career events.
2. "GENERAL": Monitor/search anything else (tech conferences, VC rounds, flights, product drops, etc.).
3. "SEARCH_SAVED": User specifically asks to search or filter inside their already saved/found opportunities board.
4. "STOP": User asks to stop/terminate/delete a running scout.
5. "INVALID": Pure gibberish or un-actionable greeting/noise without a search request.

Rules:
- If CAREER: create a concise `refined_instruction` tailored to skills/preferences to find live opportunities.
- If GENERAL: create a concise `refined_instruction` tailored to what they requested.
- If INVALID or STOP: set `refined_instruction` to null and include a bilingual (EN + HI) `reply` if INVALID.

Return STRICT JSON:
{{
  "intent": "CAREER | GENERAL | SEARCH_SAVED | STOP | INVALID",
  "refined_instruction": "string or null",
  "category": "CAREER | GENERAL",
  "search_keywords": "string or null",
  "reply": "string or null"
}}"""
    text, _ = await _ai_generate_text(prompt, json_mode=True, max_tokens=500)
    if text:
        try:
            parsed = _safe_json_loads(text)
            if isinstance(parsed, dict) and parsed.get("intent"):
                return parsed
        except Exception:
            pass
    return _heuristic_intent_fallback(raw_text, user_profile, learned_preferences)


_KNOWN_PROFILE_SKILLS = [
    "Python",
    "JavaScript",
    "TypeScript",
    "React",
    "Next.js",
    "Node.js",
    "FastAPI",
    "Django",
    "Flask",
    "Java",
    "C++",
    "C",
    "Go",
    "Rust",
    "SQL",
    "PostgreSQL",
    "MySQL",
    "MongoDB",
    "Redis",
    "AWS",
    "GCP",
    "Azure",
    "Docker",
    "Kubernetes",
    "Git",
    "Linux",
    "Tailwind CSS",
    "HTML",
    "CSS",
    "Machine Learning",
    "Deep Learning",
    "AI",
    "NLP",
    "PyTorch",
    "TensorFlow",
    "Data Science",
    "Figma",
]


def _heuristic_profile_fallback(raw_text: str) -> dict[str, Any]:
    """Deterministic fallback extractor when LLM output is unavailable or malformed."""
    compact_text = re.sub(r"\s+", " ", raw_text).strip()
    lowered = compact_text.lower()
    known_skills = []
    for skill in _KNOWN_PROFILE_SKILLS:
        pattern = rf"(?<![a-z0-9]){re.escape(skill.lower())}(?![a-z0-9])"
        if re.search(pattern, lowered):
            known_skills.append(skill)

    words = [w.strip(",.()[]{}:;") for w in compact_text.split() if len(w.strip(",.()[]{}:;")) > 2]
    exp_level = "Junior"
    if any(k in lowered for k in ("b.tech", "b.e", "bachelor", "undergraduate", "student", "university", "college", "semester")):
        exp_level = "Student"
    elif any(k in lowered for k in ("senior", "lead", "5+ years", "6+ years")):
        exp_level = "Senior"

    locations = [loc for loc in ("India", "Bengaluru", "Hyderabad", "Pune", "Mumbai", "Delhi", "Chennai", "Remote") if loc.lower() in lowered]
    if "Remote" not in locations:
        locations.append("Remote")
    if "India" not in locations:
        locations.append("India")

    role_candidates = [
        ("Software Engineering Intern", ("software", "developer", "engineering", "full stack", "backend", "frontend")),
        ("AI / ML Engineer", ("ai", "machine learning", "deep learning", "nlp", "data science")),
        ("Hackathon Builder", ("hackathon", "prototype", "open source")),
    ]
    target_roles = [role for role, cues in role_candidates if any(cue in lowered for cue in cues)] or [
        "Internships",
        "Hackathons",
        "Full-Time Roles",
    ]

    return {
        "skills": known_skills or words[:8],
        "experience_level": exp_level,
        "education": [],
        "experience": [],
        "project_themes": known_skills[:4],
        "interests": known_skills[:4] or ["Software Engineering"],
        "preferred_locations": locations,
        "target_roles": target_roles,
        "summary": compact_text[:400],
    }


def _coerce_str_list(value: Any, limit: int = 15) -> list[str]:
    if isinstance(value, str):
        items = [part.strip() for part in value.split(",") if part.strip()]
    elif isinstance(value, list):
        items = [str(item).strip() for item in value if str(item).strip()]
    else:
        items = []
    return items[:limit]


def _normalize_profile_dict(parsed: dict[str, Any], raw_text: str) -> dict[str, Any]:
    fallback = _heuristic_profile_fallback(raw_text)
    return {
        "skills": _coerce_str_list(parsed.get("skills"), 20) or fallback["skills"],
        "experience_level": str(parsed.get("experience_level") or fallback["experience_level"]).strip() or fallback["experience_level"],
        "education": _coerce_str_list(parsed.get("education"), 8),
        "experience": _coerce_str_list(parsed.get("experience"), 8),
        "project_themes": _coerce_str_list(parsed.get("project_themes"), 10) or fallback["project_themes"],
        "interests": _coerce_str_list(parsed.get("interests"), 10) or fallback["interests"],
        "preferred_locations": _coerce_str_list(parsed.get("preferred_locations"), 10) or fallback["preferred_locations"],
        "target_roles": _coerce_str_list(parsed.get("target_roles"), 10) or fallback["target_roles"],
        "summary": str(parsed.get("summary") or fallback["summary"]).strip()[:600],
    }


async def build_user_profile(raw_text: str) -> dict[str, Any] | None:
    if not raw_text or not raw_text.strip():
        return None
    if not gemini_client:
        return _heuristic_profile_fallback(raw_text)

    prompt = f"""Analyze this resume/profile text and extract structured career profile data.

Text:
{raw_text[:10000]}

Return STRICT compact JSON only (no markdown fences, max 15 skills, max 5 items per other list, concise summary <= 3 sentences):
{{
  "skills": ["Python", "React", "FastAPI"],
  "experience_level": "Student | Junior | Mid | Senior",
  "education": ["Degree & Institution"],
  "experience": ["Role at Company - concise summary"],
  "project_themes": ["AI/ML", "Full Stack"],
  "interests": ["Internships", "Backend", "AI"],
  "preferred_locations": ["Remote", "Bengaluru", "India"],
  "target_roles": ["Software Engineering Intern", "AI Engineer"],
  "summary": "2-3 sentence professional overview."
}}"""
    text, _ = await _ai_generate_text(prompt, json_mode=True, max_tokens=2000)
    if text:
        try:
            parsed = _safe_json_loads(text)
            if isinstance(parsed, dict):
                return _normalize_profile_dict(parsed, raw_text)
        except Exception as exc:
            print(f"[PROFILE] Error parsing profile JSON ({exc}); using deterministic fallback.", flush=True)
    return _heuristic_profile_fallback(raw_text)


def _fallback_parse_raw_findings(
    raw_findings: str,
    citations: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    urls = re.findall(r"https?://[^\s\)\]>\"']+", raw_findings)
    if not urls and citations:
        for cit in citations:
            if isinstance(cit, dict) and cit.get("url"):
                urls.append(str(cit["url"]))
    if not urls:
        return []

    first_url = urls[0].rstrip(".,;")
    lines = [re.sub(r"^user input:\s*", "", ln.strip("-•* \t"), flags=re.I) for ln in raw_findings.splitlines() if ln.strip()]
    title = (lines[0][:120] if lines else "Opportunity Update") or "Opportunity Update"
    summary_en = "\n".join(f"- {ln}" for ln in lines[:5])[:900]
    return [
        {
            "title": title,
            "title_hi": title,
            "organization": "See official link",
            "category": "opportunity",
            "location": "See official link",
            "stipend_or_reward": "See official link",
            "eligibility": "See official link",
            "application_steps": "Visit the official URL below to apply.",
            "summary_en": summary_en or raw_findings[:500],
            "summary_hi": f"नया अवसर मिला: {title}\nआवेदन और पूरी जानकारी के लिए नीचे दिए गए आधिकारिक लिंक पर जाएं।",
            "full_details_en": raw_findings[:2500],
            "full_details_hi": f"शीर्षक: {title}\nविवरण: {raw_findings[:1200]}",
            "url": first_url,
            "deadline_iso": None,
            "semantic_relevance": 0.65,
            "reasoning": "Matched active search criteria.",
        }
    ]


async def _fallback_parse_with_hindi(
    raw_findings: str,
    citations: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    items = _fallback_parse_raw_findings(raw_findings, citations)
    for item in items[:2]:
        hi_title, hi_sum = await asyncio.gather(
            translate_to_hindi_sarvam(item.get("title", "")),
            translate_to_hindi_sarvam(item.get("summary_en", "")),
        )
        if hi_title:
            item["title_hi"] = hi_title
        if hi_sum:
            item["summary_hi"] = hi_sum
            item["full_details_hi"] = f"{hi_title or item.get('title', '')}\n{hi_sum}"
    return items


async def parse_yutori_findings(
    raw_findings: str,
    task_type: str = "CAREER",
    user_profile: dict[str, Any] | None = None,
    learned_preferences: dict[str, Any] | None = None,
    citations: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if not raw_findings or not raw_findings.strip():
        return []

    citations_ctx = json.dumps(citations[:15], default=str) if citations else "[]"
    profile_ctx = _personalization_context(user_profile, learned_preferences)

    parse_prompt = f"""Extract ALL distinct, valid opportunities or findings from this web crawl output and its citations.

Raw Crawl Output:
{raw_findings[:16000]}

Citations:
{citations_ctx}

Task Type: {task_type}
Candidate Profile & Learned Preferences: {profile_ctx}

CRITICAL RULES:
1. Only include REAL opportunities with a valid http/https application or info URL (check text and citations).
   Do NOT invent URLs or emit placeholder items ("no opportunities found", etc.).
2. Provide BOTH English (`summary_en`, `full_details_en`) AND natural Hindi Devanagari
   (`title_hi`, `summary_hi`, `full_details_hi`) for every item.
3. Keep `summary_en` and `summary_hi` crisp, bulleted (`- `), factual, without emojis or filler,
   and under 800 characters each.
4. Put comprehensive untruncated info in `full_details_en` and `full_details_hi`
   (eligibility, stipend/prize, dates, location, selection process, prerequisites, benefits).
5. Extract deadline in strict ISO 8601 UTC format (e.g. "2026-12-31T23:59:59Z") if mentioned, else null.
6. Score `semantic_relevance` (0.0 to 1.0) against the Candidate Profile & Learned Preferences
   (if anonymous, use 0.7) and give a 1-sentence `reasoning`.

Return STRICT JSON array:
[
  {{
    "title": "Exact Canonical Opportunity Title",
    "title_hi": "हिंदी में शीर्षक",
    "organization": "Company / Organizer",
    "category": "internship | job | hackathon | scholarship | conference | general",
    "location": "Remote / City / Country",
    "stipend_or_reward": "Compensation / Stipend / Prize or Not specified",
    "eligibility": "Who is eligible",
    "application_steps": "How to apply",
    "summary_en": "- Bullet summary in English",
    "summary_hi": "- हिंदी में मुख्य विवरण",
    "full_details_en": "Detailed description in English",
    "full_details_hi": "हिंदी में पूरी जानकारी",
    "url": "https://...",
    "deadline_iso": "2026-12-31T23:59:59Z or null",
    "semantic_relevance": 0.85,
    "reasoning": "1-sentence match reasoning"
  }}
]"""
    text, _ = await _ai_generate_text(parse_prompt, json_mode=True, max_tokens=1600)
    if text:
        try:
            parsed = _safe_json_loads(text)
            if isinstance(parsed, dict):
                parsed = [parsed]
            if isinstance(parsed, list) and parsed:
                return parsed
        except Exception as exc:
            print(f"[PARSER] AI JSON parse failed ({exc}); using fallback parser.", flush=True)
    return await _fallback_parse_with_hindi(raw_findings, citations)


async def instant_opportunity_search(
    query_text: str,
    user_profile: dict[str, Any] | None = None,
    learned_preferences: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Performs immediate live multi-source discovery via LiveScout (Unstop, Devpost, Remotive, Arbeitnow, RSS)."""
    live_res = await search_live_opportunities(
        query_text=query_text,
        user_profile=user_profile,
        learned_preferences=learned_preferences,
        limit=5,
    )
    if live_res.get("items"):
        return live_res

    if not gemini_client:
        return {"raw_text": "", "items": [], "citations": []}

    profile_ctx = _personalization_context(user_profile, learned_preferences)
    search_prompt = f"""You are OppTrax Instant Discovery Agent.
Use Google Search to find 3 to 5 currently relevant, real, clickable opportunities matching this request:
Request: "{query_text}"
Candidate Profile & Preferences: {profile_ctx}

For EACH opportunity found, provide exact title, organization, category, location,
stipend/reward, eligibility, closing deadline, detailed overview, application steps, and REAL official URL.
Do NOT invent links or fake opportunities."""
    raw_text, _ = await _try_gemini_text(search_prompt, use_google_search=True)
    if not raw_text:
        return {"raw_text": "", "items": [], "citations": []}
    items = await parse_yutori_findings(
        raw_findings=raw_text,
        task_type="CAREER",
        user_profile=user_profile,
        learned_preferences=learned_preferences,
    )
    return {"raw_text": raw_text, "items": items, "citations": []}


def _offline_qa_answer(opp: dict[str, Any], target_lang: str) -> str:
    if target_lang == "hi":
        return (
            f"*{opp.get('title_hi') or opp.get('title')}* के बारे में विवरण:\n"
            f"• *संस्था:* {opp.get('organization') or 'उल्लिखित नहीं'}\n"
            f"• *स्थान:* {opp.get('location') or 'उल्लिखित नहीं'}\n"
            f"• *स्टाइपेंड/पुरस्कार:* {opp.get('stipend_or_reward') or 'उल्लिखित नहीं'}\n"
            f"• *पात्रता:* {opp.get('eligibility') or 'आधिकारिक लिंक देखें'}\n"
            f"• *अंतिम तिथि:* {opp.get('deadline_iso') or 'उल्लिखित नहीं'}\n"
            f"• *सारांश:* {opp.get('summary_hi') or opp.get('summary_en') or opp.get('summary')}\n"
            f"• *लिंक:* {opp.get('url')}"
        )
    if target_lang == "en":
        return (
            f"Details for *{opp.get('title')}*:\n"
            f"• *Organization:* {opp.get('organization') or 'Not specified'}\n"
            f"• *Location:* {opp.get('location') or 'Not specified'}\n"
            f"• *Stipend/Reward:* {opp.get('stipend_or_reward') or 'Not specified'}\n"
            f"• *Eligibility:* {opp.get('eligibility') or 'Check official link'}\n"
            f"• *Deadline:* {opp.get('deadline_iso') or 'Not specified'}\n"
            f"• *Summary:* {opp.get('full_details_en') or opp.get('summary')}\n"
            f"• *Link:* {opp.get('url')}"
        )
    dl_en = opp.get("deadline_iso") or "Not specified"
    st_en = opp.get("stipend_or_reward") or "N/A"
    dl_hi = opp.get("deadline_iso") or "उल्लिखित नहीं"
    return (
        f"*[English] {opp.get('title')}*\n"
        f"• *Deadline:* {dl_en} | *Stipend:* {st_en}\n"
        f"• *Eligibility:* {opp.get('eligibility') or 'Check official link'}\n"
        f"• {opp.get('summary_en') or opp.get('summary')}\n\n"
        f"*[हिंदी] {opp.get('title_hi') or opp.get('title')}*\n"
        f"• *अंतिम तिथि:* {dl_hi} | *स्टाइपेंड:* {st_en}\n"
        f"• {opp.get('summary_hi') or opp.get('summary')}\n"
        f"• *Link:* {opp.get('url')}"
    )


def _is_auth_or_quota_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(
        marker in msg
        for marker in ("401", "403", "unauthenticated", "api key not valid", "api_key_invalid", "quota")
    )


def _offline_application_draft(opp: dict[str, Any], user_profile: dict[str, Any] | None) -> str:
    title = opp.get("title", "this role")
    skills = ", ".join((user_profile or {}).get("skills", []) or ["relevant technical skills"])
    return (
        f"*Application Strategy for {title}*\n"
        f"- Highlight your core strengths in: {skills}\n"
        f"- Reference the key eligibility & requirements: {opp.get('eligibility', 'role specifications')}\n"
        f"- Apply before {opp.get('deadline_iso') or 'the closing date'} at {opp.get('url', '')}"
    )


async def answer_opportunity_question(
    opp: dict[str, Any],
    question: str,
    user_profile: dict[str, Any] | None = None,
    chat_history: list[dict[str, str]] | None = None,
    preferred_language: str = "both",
    force_web_search: bool = False,
) -> dict[str, Any]:
    target_lang = detect_query_language(question, preferred_language)
    lang_directive = language_instruction_for_prompt(target_lang)

    opp_context = (
        f"Title: {opp.get('title')}\n"
        f"Organization: {opp.get('organization')}\n"
        f"Category: {opp.get('category')}\n"
        f"Location: {opp.get('location')}\n"
        f"Stipend/Reward: {opp.get('stipend_or_reward')}\n"
        f"Eligibility: {opp.get('eligibility')}\n"
        f"Deadline: {opp.get('deadline_iso')}\n"
        f"Application Steps: {opp.get('application_steps')}\n"
        f"URL: {opp.get('url')}\n"
        f"Summary (EN): {opp.get('full_details_en') or opp.get('summary_en') or opp.get('summary')}\n"
        f"Summary (HI): {opp.get('full_details_hi') or opp.get('summary_hi')}\n"
        f"Raw Source Content: {str(opp.get('raw_content') or '')[:8000]}"
    )

    history_text = ""
    if chat_history:
        history_text = "\n".join(
            f"{m.get('role', 'user').upper()}: {m.get('content') or m.get('text') or ''}"
            for m in chat_history[-6:]
        )

    live_detail = ""
    if force_web_search and opp.get("url"):
        live_detail = await fetch_deep_opportunity_context(opp)
        if not live_detail and not opp.get("raw_content"):
            live_detail = await fetch_url_text(str(opp["url"]))

    rag_prompt = f"""You are OppTrax AI Opportunity Copilot.
Answer accurately using the opportunity data, candidate profile, and live source details
(official deadlines, compensation, selection rounds, company info).

LANGUAGE REQUIREMENT:
{lang_directive}

Formatting Rules:
- Format cleanly for WhatsApp & web chat (*bold* for labels, `- ` or `• ` for bullets, no `#` markdown headings).
- Be concise, accurate, and directly answer the user's question. Include the official link when relevant.

Candidate Profile:
{user_profile or 'Not provided'}

Opportunity Data:
{opp_context}

Live Source Verification:
{live_detail[:4500] or 'Using verified opportunity snapshot above'}

Recent Conversation:
{history_text or 'None'}

Candidate Question: {question}"""

    text, grounded = await _ai_generate_text(rag_prompt, use_google_search=force_web_search, max_tokens=1100)
    if text:
        return {"answer": text, "language": target_lang, "grounded": grounded or bool(live_detail)}
    return {"answer": _offline_qa_answer(opp, target_lang), "language": target_lang, "grounded": False}


async def generate_application_draft(
    opp: dict[str, Any],
    user_profile: dict[str, Any] | None = None,
    preferred_language: str = "en",
) -> str:
    lang = normalize_language(preferred_language, default="en")
    lang_directive = language_instruction_for_prompt(lang)

    draft_prompt = f"""Cross-reference the Candidate Profile: {user_profile or 'General applicant'}
with this Opportunity:
Title: {opp.get('title')}
Organization: {opp.get('organization')}
Requirements/Content: {opp.get('full_details_en') or opp.get('summary') or opp.get('raw_content', '')}

LANGUAGE REQUIREMENT: {lang_directive}

Compile an actionable application package containing:
1. A tailored Cover Letter / Statement of Interest.
2. 3-4 strategic bullet points on how to position their skills to stand out.
Format cleanly for WhatsApp (*bold*, bullet points, no markdown `#` headers)."""
    text, _ = await _ai_generate_text(draft_prompt, max_tokens=1000)
    return text or _offline_application_draft(opp, user_profile)


async def extract_opportunity_from_input(raw_input: str) -> dict[str, Any] | None:
    """Extracts structured bilingual opportunity data from a user-submitted URL or text snippet."""
    url_match = re.search(r"https?://[^\s]+", raw_input)
    found_url = url_match.group(0).rstrip(".,;)") if url_match else None
    fetched_text = await fetch_url_text(found_url) if found_url else ""
    combined = f"User Input: {raw_input}\n\nFetched Webpage Content:\n{fetched_text}".strip()

    parsed_items = await parse_yutori_findings(
        raw_findings=combined,
        task_type="CAREER",
        citations=[{"url": found_url}] if found_url else None,
    )
    if parsed_items:
        item = parsed_items[0]
        if found_url and (not item.get("url") or not str(item.get("url")).startswith("http")):
            item["url"] = found_url
        item["raw_content"] = combined[:12000]
        return item
    return None


async def enrich_findings_with_websearch(
    findings_list: list[dict[str, Any]],
    preferred_language: str = "both",
) -> str:
    if not findings_list:
        return "No findings to enrich."
    lang_directive = language_instruction_for_prompt(preferred_language)
    findings_context = "\n".join(f"- {f.get('text', '')}" for f in findings_list)
    prompt = f"""You are OppTrax, an autonomous opportunity research agent.
Findings:
{findings_context}

Task:
1. Verify companies/organizers, stipends/prizes, eligibility, and exact deadlines.
2. Synthesize cleanly for WhatsApp (*bold* headings, bullet points, no `#` headers).
3. Include the direct URL for every finding.
4. {lang_directive}
5. Start immediately with the first finding without conversational filler."""

    text, _ = await _ai_generate_text(prompt, use_google_search=True, max_tokens=1100)
    return text or findings_context
