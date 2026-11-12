import asyncio
import html
import json
import re
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import quote_plus

import requests

from config import translate_to_hindi_sarvam

LIVE_SCOUT_NETWORK_ENABLED = True

_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) OppTrax-LiveScout/2.0"}

_STOPWORDS = {
    "find",
    "search",
    "for",
    "me",
    "my",
    "the",
    "and",
    "with",
    "currently",
    "open",
    "newly",
    "announced",
    "matching",
    "profile",
    "opportunities",
    "opportunity",
    "extract",
    "exact",
    "title",
    "official",
    "links",
    "details",
    "candidate",
    "skills",
    "preferred",
    "topics",
    "please",
    "show",
    "get",
    "track",
    "monitor",
    "india",
    "fellowships",
    "roles",
}

_HINDI_TO_EN_TERMS = {
    "इंटर्नशिप": "internship",
    "नौकरी": "software job",
    "हैकाथॉन": "hackathon",
    "छात्रवृत्ति": "scholarship",
    "फेलोशिप": "fellowship",
    "पायथन": "python",
    "रिमोट": "remote",
    "बेंगलुरु": "bengaluru",
    "अनुदान": "grant",
    "खोजो": "",
    "मेरे": "",
    "लिए": "",
}


def _strip_html(raw: str | None, limit: int = 1600) -> str:
    if not raw:
        return ""
    text = html.unescape(str(raw))
    text = re.sub(r"<li[^>]*>", "\n- ", text, flags=re.IGNORECASE)
    text = re.sub(r"<br\s*/?>|</p>|</div>|</h[1-6]>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:limit]


def _extract_tokens(query_text: str, user_profile: dict[str, Any] | None = None) -> tuple[list[str], str]:
    raw_words = re.findall(r"[a-zA-Z0-9+#.-]+|[\u0900-\u097F]+", (query_text or "").lower())
    mapped: list[str] = []
    for word in raw_words:
        repl = _HINDI_TO_EN_TERMS.get(word, word)
        for part in repl.split():
            if len(part) >= 2 and part not in _STOPWORDS and part not in mapped:
                mapped.append(part)

    skills = [str(s).strip().lower() for s in (user_profile or {}).get("skills", []) if str(s).strip()]
    for skill in skills[:3]:
        if len(mapped) < 4 and skill not in mapped:
            mapped.append(skill)

    if not mapped:
        mapped = ["ai", "software", "internship"]
    primary_query = " ".join(mapped[:4])
    return mapped, primary_query


def _select_tracks(tokens: set[str]) -> dict[str, bool]:
    joined = " ".join(tokens)
    has_hack = any(k in joined for k in ("hack", "hackathon", "coding", "challenge", "competition", "devpost"))
    has_intern = any(k in joined for k in ("intern", "internship", "student", "fresher"))
    has_job = any(k in joined for k in ("job", "role", "engineer", "developer", "remote", "hiring", "fulltime"))
    has_conf = any(k in joined for k in ("conf", "conference", "summit", "meetup", "event"))
    has_general = any(k in joined for k in ("fellowship", "scholarship", "grant", "vc", "seed", "flight", "funding"))

    any_specific = has_hack or has_intern or has_job or has_conf or has_general
    return {
        "unstop_hackathons": has_hack or not any_specific,
        "unstop_internships": has_intern or not any_specific,
        "unstop_jobs": has_job,
        "unstop_conferences": has_conf,
        "devpost": has_hack or "ai" in tokens or not any_specific,
        "remotive": has_job or "remote" in tokens or "python" in tokens or "ai" in tokens,
        "arbeitnow": has_job or not any_specific,
        "web_fallback": has_general or has_conf,
    }


def _format_unstop_reward(item: dict[str, Any]) -> str:
    prizes = item.get("prizes") or []
    if isinstance(prizes, list) and prizes:
        parts = []
        for p in prizes[:2]:
            cash = p.get("cash")
            curr = p.get("currencyCode") or "INR"
            others = p.get("others")
            if cash:
                parts.append(f"{curr} {cash:,}" if isinstance(cash, (int, float)) else f"{curr} {cash}")
            elif others:
                parts.append(str(others)[:60])
        if parts:
            return " | ".join(parts)

    job = item.get("jobDetail") or {}
    if isinstance(job, dict):
        min_s, max_s = job.get("min_salary"), job.get("max_salary")
        pay_in = job.get("pay_in") or "month"
        if min_s or max_s:
            band = f"{min_s or max_s}" if not (min_s and max_s and min_s != max_s) else f"{min_s} - {max_s}"
            return f"INR {band}/{pay_in}"
        if job.get("paid_unpaid") == "paid":
            return "Paid (Check official link)"
    return "See official link"


def _format_unstop_location(item: dict[str, Any]) -> str:
    locs = item.get("locations") or []
    if isinstance(locs, list) and locs and isinstance(locs[0], dict):
        parts = [p for p in (locs[0].get("city"), locs[0].get("state"), locs[0].get("country")) if p]
        if parts:
            return ", ".join(parts)
    job = item.get("jobDetail") or {}
    if isinstance(job, dict) and job.get("type") == "wfh":
        return "Remote / WFH"
    return "Online" if str(item.get("region", "")).lower() == "online" else "India / Hybrid"


def _parse_unstop_eligibility(item: dict[str, Any]) -> str:
    skills = [
        str(s.get("skill_name") or s.get("skill") or "").strip()
        for s in (item.get("required_skills") or [])
        if isinstance(s, dict)
    ]
    skills = [s for s in skills if s][:6]
    skill_str = f"Skills: {', '.join(skills)}" if skills else ""
    req = item.get("regnRequirements") or {}
    raw_elig = req.get("eligibility")
    extra = ""
    if isinstance(raw_elig, str) and raw_elig.startswith("{"):
        try:
            parsed = json.loads(raw_elig)
            sectors = parsed.get("sector") or []
            if sectors:
                extra = f"Open to: {', '.join(str(x) for x in sectors[:3])}"
        except ValueError:
            pass
    joined = " | ".join(p for p in (extra, skill_str) if p)
    return joined or "Check official Unstop eligibility criteria"


def _normalize_unstop_item(item: dict[str, Any], kind: str) -> dict[str, Any] | None:
    title = str(item.get("title") or "").strip()
    url = str(item.get("seo_url") or "").strip()
    if not url and item.get("public_url"):
        url = f"https://unstop.com/{str(item['public_url']).lstrip('/')}"
    if not title or not url.startswith("http"):
        return None

    org = ((item.get("organisation") or {}).get("name") or "Unstop Organizer").strip()
    req = item.get("regnRequirements") or {}
    deadline = req.get("end_regn_dt") or item.get("end_date")
    cat_map = {
        "hackathons": "Hackathon",
        "internships": "Internship",
        "jobs": "Job",
        "conferences": "Conference",
    }
    category = cat_map.get(kind, "Opportunity")
    location = _format_unstop_location(item)
    reward = _format_unstop_reward(item)
    elig = _parse_unstop_eligibility(item)
    raw_details = _strip_html(item.get("details"), 1800)
    remain = req.get("remain_days") or ""

    summary_lines = [
        f"- {category} by {org} ({location})",
        f"- Compensation / Prize: {reward}" + (f" | Status: {remain}" if remain else ""),
    ]
    if raw_details:
        summary_lines.append(f"- {raw_details[:260]}")

    return {
        "title": title,
        "title_hi": title,
        "organization": org,
        "category": category,
        "location": location,
        "stipend_or_reward": reward,
        "eligibility": elig,
        "application_steps": "Register and submit your application directly on Unstop.",
        "summary_en": "\n".join(summary_lines),
        "summary_hi": f"- {title}\n- संस्था: {org} | स्थान: {location} | पुरस्कार/स्टाइपेंड: {reward}",
        "full_details_en": raw_details or "\n".join(summary_lines),
        "full_details_hi": f"{title} — {org} ({location})\nपुरस्कार/स्टाइपेंड: {reward}\nपात्रता: {elig}",
        "url": url,
        "deadline_iso": str(deadline) if deadline else None,
        "source_name": "Unstop",
    }


def _fetch_unstop_track_sync(kind: str, tokens: list[str], limit: int = 5) -> list[dict[str, Any]]:
    terms = [" ".join(tokens[:2]), tokens[0]] if len(tokens) >= 2 else [tokens[0]]
    seen_terms: list[str] = []
    for t in terms:
        if t and t not in seen_terms:
            seen_terms.append(t)

    for term in seen_terms:
        url = (
            "https://unstop.com/api/public/opportunity/search-result"
            f"?opportunity={quote_plus(kind)}&per_page={limit}&searchTerm={quote_plus(term)}"
        )
        try:
            res = requests.get(url, headers=_HEADERS, timeout=10)
            if res.status_code != 200:
                continue
            rows = ((res.json().get("data") or {}).get("data")) or []
            if rows:
                items = [_normalize_unstop_item(r, kind) for r in rows[:limit] if isinstance(r, dict)]
                cleaned = [it for it in items if it]
                if cleaned:
                    return cleaned
        except Exception as exc:
            print(f"[LIVESCOUT UNSTOP:{kind}] Fetch error: {exc}", flush=True)
    return []


def _fetch_devpost_sync(tokens: list[str], limit: int = 4) -> list[dict[str, Any]]:
    term = tokens[0] if tokens else "ai"
    url = f"https://devpost.com/api/hackathons?search={quote_plus(term)}&per_page={limit}"
    out: list[dict[str, Any]] = []
    try:
        res = requests.get(url, headers=_HEADERS, timeout=10)
        if res.status_code != 200:
            return []
        for h in (res.json().get("hackathons") or [])[:limit]:
            if h.get("open_state") not in {None, "open", "upcoming"}:
                continue
            title = str(h.get("title") or "").strip()
            link = str(h.get("url") or "").strip()
            if not title or not link.startswith("http"):
                continue
            prize = _strip_html(h.get("prize_amount"), 80) or "See Devpost page"
            loc = ((h.get("displayed_location") or {}).get("location") or "Online").strip()
            dates = h.get("submission_period_dates") or ""
            time_left = h.get("time_left_to_submission") or ""
            themes = ", ".join(t.get("name", "") for t in (h.get("themes") or []) if isinstance(t, dict))
            summary = f"- Global hackathon ({loc}) | Prize: {prize}\n- Timeline: {dates} ({time_left})"
            if themes:
                summary += f"\n- Themes: {themes}"
            out.append(
                {
                    "title": title,
                    "title_hi": title,
                    "organization": h.get("organization_name") or "Devpost Organizer",
                    "category": "Hackathon",
                    "location": loc,
                    "stipend_or_reward": prize,
                    "eligibility": themes or "Global developers & students",
                    "application_steps": "Join and submit project via Devpost before submission deadline.",
                    "summary_en": summary,
                    "summary_hi": f"- {title} ({loc})\n- पुरस्कार: {prize} | समय सीमा: {time_left or dates}",
                    "full_details_en": f"{title}\n{summary}",
                    "full_details_hi": f"{title}\nस्थान: {loc}\nपुरस्कार: {prize}\nसमय सीमा: {dates}",
                    "url": link,
                    "deadline_iso": None,
                    "source_name": "Devpost",
                }
            )
    except Exception as exc:
        print(f"[LIVESCOUT DEVPOST] Fetch error: {exc}", flush=True)
    return out


def _fetch_remotive_sync(tokens: list[str], limit: int = 4) -> list[dict[str, Any]]:
    term = " ".join(tokens[:2]) if tokens else "python"
    url = f"https://remotive.com/api/remote-jobs?search={quote_plus(term)}&limit={limit}"
    out: list[dict[str, Any]] = []
    try:
        res = requests.get(url, headers=_HEADERS, timeout=10)
        if res.status_code != 200:
            return []
        for job in (res.json().get("jobs") or [])[:limit]:
            title = str(job.get("title") or "").strip()
            link = str(job.get("url") or "").strip()
            if not title or not link.startswith("http"):
                continue
            org = str(job.get("company_name") or "Remote Company").strip()
            loc = str(job.get("candidate_required_location") or "Worldwide Remote").strip()
            sal = str(job.get("salary") or "Competitive / See link").strip()
            tags = ", ".join(str(t) for t in (job.get("tags") or [])[:5])
            desc = _strip_html(job.get("description"), 1600)
            summary = f"- Remote {job.get('category', 'Tech')} role at {org} ({loc})\n- Compensation: {sal}"
            if tags:
                summary += f"\n- Stack: {tags}"
            out.append(
                {
                    "title": title,
                    "title_hi": title,
                    "organization": org,
                    "category": "Job",
                    "location": loc,
                    "stipend_or_reward": sal,
                    "eligibility": tags or "See role qualifications",
                    "application_steps": "Submit application on Remotive / official company link.",
                    "summary_en": summary,
                    "summary_hi": f"- {title} — {org} ({loc})\n- वेतन: {sal}",
                    "full_details_en": desc or summary,
                    "full_details_hi": f"{title} — {org}\nस्थान: {loc}\nवेतन: {sal}\nमुख्य विवरण: {desc[:500]}",
                    "url": link,
                    "deadline_iso": None,
                    "source_name": "Remotive",
                }
            )
    except Exception as exc:
        print(f"[LIVESCOUT REMOTIVE] Fetch error: {exc}", flush=True)
    return out


def _fetch_arbeitnow_sync(tokens: list[str], limit: int = 4) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    try:
        res = requests.get("https://www.arbeitnow.com/api/job-board-api?page=1", headers=_HEADERS, timeout=10)
        if res.status_code != 200:
            return []
        tok_set = set(tokens)
        for job in res.json().get("data") or []:
            blob = f"{job.get('title', '')} {' '.join(job.get('tags') or [])} {job.get('location', '')}".lower()
            if tok_set and not any(t in blob for t in tok_set):
                continue
            title = str(job.get("title") or "").strip()
            link = str(job.get("url") or "").strip()
            if not title or not link.startswith("http"):
                continue
            org = str(job.get("company_name") or "Tech Employer").strip()
            loc = "Remote" if job.get("remote") else str(job.get("location") or "Europe / Global").strip()
            tags = ", ".join(str(t) for t in (job.get("tags") or [])[:5])
            desc = _strip_html(job.get("description"), 1600)
            summary = f"- Tech opening at {org} ({loc})" + (f"\n- Tags: {tags}" if tags else "")
            out.append(
                {
                    "title": title,
                    "title_hi": title,
                    "organization": org,
                    "category": "Job",
                    "location": loc,
                    "stipend_or_reward": "See official listing",
                    "eligibility": tags or "Tech / engineering candidates",
                    "application_steps": "Apply directly on the official Arbeitnow link.",
                    "summary_en": summary,
                    "summary_hi": f"- {title} — {org} ({loc})",
                    "full_details_en": desc or summary,
                    "full_details_hi": f"{title} — {org} ({loc})\n{desc[:500]}",
                    "url": link,
                    "deadline_iso": None,
                    "source_name": "Arbeitnow",
                }
            )
            if len(out) >= limit:
                break
    except Exception as exc:
        print(f"[LIVESCOUT ARBEITNOW] Fetch error: {exc}", flush=True)
    return out


def _build_web_feed_item(
    title: str,
    link: str,
    org: str,
    location: str,
    source_name: str,
    summary_en: str,
    full_details_en: str,
) -> dict[str, Any]:
    return {
        "title": title,
        "title_hi": title,
        "organization": org,
        "category": "Opportunity",
        "location": location,
        "stipend_or_reward": "See announcement",
        "eligibility": "Check official announcement link",
        "application_steps": "Open official announcement link for eligibility and application steps.",
        "summary_en": summary_en,
        "summary_hi": f"- {title}\n- लिंक: {link}",
        "full_details_en": full_details_en,
        "full_details_hi": f"{title}\nआधिकारिक लिंक: {link}",
        "url": link,
        "deadline_iso": None,
        "source_name": source_name,
    }


def _fetch_hn_sync(query_str: str, limit: int = 4) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    try:
        hn_url = f"https://hn.algolia.com/api/v1/search_by_date?query={quote_plus(query_str)}&tags=story&hitsPerPage={limit}"
        res = requests.get(hn_url, headers=_HEADERS, timeout=8)
        if res.status_code != 200:
            return out
        for hit in res.json().get("hits") or []:
            title = str(hit.get("title") or "").strip()
            link = str(hit.get("url") or "").strip()
            if title and link.startswith("http"):
                out.append(
                    _build_web_feed_item(
                        title=title,
                        link=link,
                        org="HackerNews / Web Discovery",
                        location="Global / Online",
                        source_name="HackerNews",
                        summary_en=f"- {title}\n- Source: {link}",
                        full_details_en=f"{title}\nOfficial source: {link}",
                    )
                )
    except Exception as exc:
        print(f"[LIVESCOUT HN] Fetch error: {exc}", flush=True)
    return out[:limit]


def _fetch_google_rss_sync(query_str: str, limit: int = 4) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    try:
        rss_url = f"https://news.google.com/rss/search?q={quote_plus(query_str)}&hl=en-IN&gl=IN&ceid=IN:en"
        res = requests.get(rss_url, headers=_HEADERS, timeout=8)
        if res.status_code != 200:
            return out
        root = ET.fromstring(res.text)
        for el in root.findall(".//item")[:limit]:
            title = (el.findtext("title") or "").strip()
            link = (el.findtext("link") or "").strip()
            pub_date = el.findtext("pubDate") or "Recent"
            if title and link.startswith("http"):
                out.append(
                    _build_web_feed_item(
                        title=title,
                        link=link,
                        org="Google News RSS",
                        location="India / Global",
                        source_name="WebRSS",
                        summary_en=f"- {title}\n- Published: {pub_date}",
                        full_details_en=f"{title}\nPublished: {pub_date}\n{link}",
                    )
                )
    except Exception as exc:
        print(f"[LIVESCOUT RSS] Fetch error: {exc}", flush=True)
    return out[:limit]


def _fetch_hn_and_rss_sync(query_str: str, limit: int = 4) -> list[dict[str, Any]]:
    out = _fetch_hn_sync(query_str, limit=limit)
    if len(out) >= limit:
        return out[:limit]
    out.extend(_fetch_google_rss_sync(query_str, limit=limit - len(out)))
    return out[:limit]


def _score_candidate(
    item: dict[str, Any],
    tokens: list[str],
    user_profile: dict[str, Any] | None,
    learned_preferences: dict[str, Any] | None,
) -> dict[str, Any]:
    blob = " ".join(
        str(item.get(k) or "") for k in ("title", "organization", "category", "location", "eligibility", "full_details_en")
    ).lower()
    score = 0.58
    matched_terms: list[str] = []

    for tok in tokens:
        if tok in blob:
            score += 0.08
            matched_terms.append(tok)

    skills = [str(s).lower() for s in (user_profile or {}).get("skills", [])]
    matched_skills = [s for s in skills if s and s in blob]
    score += min(0.18, 0.06 * len(matched_skills))

    liked = [str(k).lower() for k in (learned_preferences or {}).get("liked_keywords", [])]
    disliked = [str(k).lower() for k in (learned_preferences or {}).get("disliked_keywords", [])]
    if any(k in blob for k in liked):
        score += 0.08
    if any(k in blob for k in disliked):
        score -= 0.15

    if item.get("deadline_iso"):
        score += 0.05

    final_score = round(max(0.15, min(0.98, score)), 2)
    src = item.get("source_name") or "LiveScout"
    reasons = []
    if matched_skills:
        reasons.append(f"matches skills ({', '.join(matched_skills[:3])})")
    elif matched_terms:
        reasons.append(f"matches query ({', '.join(matched_terms[:3])})")
    if item.get("deadline_iso"):
        reasons.append("active registration deadline")
    item["semantic_relevance"] = final_score
    item["reasoning"] = f"Live verified from {src}: " + (", ".join(reasons) or "relevant current listing") + "."
    return item


async def _enrich_item_hindi(item: dict[str, Any]) -> dict[str, Any]:
    title = item.get("title", "")
    summary = item.get("summary_en", "")
    title_hi, summary_hi = await asyncio.gather(
        translate_to_hindi_sarvam(title),
        translate_to_hindi_sarvam(summary),
    )
    if title_hi:
        item["title_hi"] = title_hi
    if summary_hi:
        item["summary_hi"] = summary_hi
        item["full_details_hi"] = f"{title_hi or title}\n{summary_hi}\nलिंक: {item.get('url', '')}"
    return item


def _collect_sources_sync(tokens: list[str], primary_query: str) -> list[dict[str, Any]]:
    tracks = _select_tracks(set(tokens))
    collected: list[dict[str, Any]] = []
    for kind, flag in (
        ("hackathons", "unstop_hackathons"),
        ("internships", "unstop_internships"),
        ("jobs", "unstop_jobs"),
        ("conferences", "unstop_conferences"),
    ):
        if tracks[flag]:
            collected.extend(_fetch_unstop_track_sync(kind, tokens, limit=4))
    if tracks["devpost"]:
        collected.extend(_fetch_devpost_sync(tokens, limit=4))
    if tracks["remotive"]:
        collected.extend(_fetch_remotive_sync(tokens, limit=4))
    if tracks["arbeitnow"]:
        collected.extend(_fetch_arbeitnow_sync(tokens, limit=3))
    if tracks["web_fallback"] or len(collected) < 3:
        collected.extend(_fetch_hn_and_rss_sync(primary_query, limit=4))
    return collected


async def search_live_opportunities(
    query_text: str,
    user_profile: dict[str, Any] | None = None,
    learned_preferences: dict[str, Any] | None = None,
    limit: int = 5,
) -> dict[str, Any]:
    """Queries live structured opportunity sources concurrently and returns ranked bilingual results."""
    if not LIVE_SCOUT_NETWORK_ENABLED:
        return {"raw_text": "", "items": [], "citations": []}

    tokens, primary_query = _extract_tokens(query_text, user_profile)
    raw_items = await asyncio.to_thread(_collect_sources_sync, tokens, primary_query)

    seen_urls: set[str] = set()
    unique_items: list[dict[str, Any]] = []
    for it in raw_items:
        url_key = str(it.get("url") or "").rstrip("/").lower()
        if url_key and url_key not in seen_urls:
            seen_urls.add(url_key)
            unique_items.append(_score_candidate(it, tokens, user_profile, learned_preferences))

    unique_items.sort(key=lambda x: x.get("semantic_relevance", 0.0), reverse=True)
    top_items = unique_items[:limit]
    if top_items:
        top_items = list(await asyncio.gather(*[_enrich_item_hindi(it) for it in top_items]))

    citations = [
        {"id": str(i), "url": it["url"], "title": f"[{it.get('source_name', 'Live')}] {it['title']}"}
        for i, it in enumerate(top_items, 1)
    ]
    raw_summary = "\n".join(f"- {it['title']} ({it.get('organization')}): {it['url']}" for it in top_items)
    return {"raw_text": raw_summary, "items": top_items, "citations": citations}


def _fetch_unstop_detail_sync(url: str) -> str:
    match = re.search(r"-(\d+)(?:[/?#]|$)", url or "")
    if not match:
        return ""
    comp_id = match.group(1)
    try:
        res = requests.get(f"https://unstop.com/api/public/competition/{comp_id}", headers=_HEADERS, timeout=10)
        if res.status_code != 200:
            return ""
        comp = (res.json().get("data") or {}).get("competition") or {}
        if not isinstance(comp, dict):
            return ""
        parts = [
            f"Title: {comp.get('title')}",
            f"Web URL: {comp.get('web_url') or url}",
            f"Region: {comp.get('region')} | Location: {comp.get('location')}",
            f"Start: {comp.get('start_date')} | End: {comp.get('end_date')}",
            f"Overview:\n{_strip_html(comp.get('details'), 4000)}",
        ]
        return "\n".join(p for p in parts if p)
    except Exception as exc:
        print(f"[LIVESCOUT DETAIL] Unstop detail fetch failed: {exc}", flush=True)
        return ""


async def fetch_deep_opportunity_context(opp: dict[str, Any]) -> str:
    """Fetches enriched live details from Unstop API or official webpage for deep research."""
    if not LIVE_SCOUT_NETWORK_ENABLED:
        return ""
    url = str(opp.get("url") or "")
    if "unstop.com" in url.lower():
        return await asyncio.to_thread(_fetch_unstop_detail_sync, url)
    return ""
