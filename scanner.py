#!/usr/bin/env python3
"""AI-assisted semiconductor opportunity scanner for SCRO at UCF.

The scanner combines public applicant-tracking-system feeds with Gemini's
grounded Google Search, uses a separate Gemini review pass to verify relevance,
suppresses duplicates, and sends new matches to a Discord incoming webhook.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import logging
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = ROOT / "companies.json"
DEFAULT_STATE = ROOT / "seen_jobs.json"
USER_AGENT = "SCRO-Opportunity-Bot/1.0 (+https://github.com/Mahkri-glitch/scro-opportunity-bot)"
DISCORD_BOT_NAME = "Jensen Huang"
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
DEFAULT_GEMINI_DISCOVERY_MODEL = "gemini-3-flash-preview"
GEMINI_API_ROOT = "https://generativelanguage.googleapis.com/v1beta/models"
GEMINI_INTERACTIONS_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"

INTERNSHIP_PATTERNS = (
    r"\bintern(ship)?\b",
    r"\bco[ -]?op\b",
)

# Eligibility is deliberately title-only. Job descriptions often contain
# generic semiconductor/manufacturing boilerplate that would otherwise allow
# unrelated internships through the filter.
TARGET_ROLE_PATTERNS = (
    (r"\bprocess(?:es|ing)?\b", "Process"),
    (r"\byield\b", "Yield"),
    (r"\bmanufactur(?:e|ing)\b", "Manufacturing"),
    (
        r"\bproduct(?:\s*/\s*test)?\s+(?:engineer(?:ing)?|development|quality|reliability|test)\b",
        "Product",
    ),
    (r"\bequipment\b", "Equipment"),
    (r"\bmetrolog(?:y|ist)\b", "Metrology"),
    (r"\bintegration\b", "Integration"),
    (r"\blithograph(?:y|ic)\b", "Lithography"),
    (r"\betch(?:ing)?\b", "Etch"),
    (r"\bdeposition\b", "Deposition"),
    (r"\bcvd\b", "CVD"),
    (r"\bpvd\b", "PVD"),
    (r"\bald\b", "ALD"),
    (r"\bcmp\b", "CMP"),
    (r"\bpackag(?:e|ing)\b", "Packaging"),
    (r"\btest(?:ing)?\b", "Test"),
    (r"\breliability\b", "Reliability"),
    (r"\bsemiconductor(?:s)?\b", "Semiconductor"),
)

SENIOR_PATTERNS = (
    r"\bsenior\b",
    r"\bsr\.?\b",
    r"\bstaff\b",
    r"\bprincipal\b",
    r"\bmanager\b",
    r"\bdirector\b",
    r"\bvice president\b",
)

HIGH_PRIORITY_TERMS = {
    "process": 4,
    "process integration": 5,
    "yield": 5,
    "manufacturing": 5,
    "fabrication": 4,
    "fab ": 3,
    "equipment": 4,
    "field service": 4,
    "metrology": 5,
    "defect": 4,
    "failure analysis": 4,
    "reliability": 3,
    "product engineer": 4,
    "test engineer": 3,
    "quality engineer": 3,
    "industrial engineer": 3,
    "semiconductor": 4,
    "thin film": 5,
    "deposition": 5,
    "lithography": 5,
    "etch": 5,
    "cmp": 5,
    "diffusion": 4,
    "implant": 4,
    "epitaxy": 5,
    "packaging": 4,
    "cleanroom": 4,
    "vacuum": 3,
    "process control": 4,
    "automation": 3,
    "facilities": 3,
}

MEDIUM_PRIORITY_TERMS = {
    "electrical": 2,
    "mechanical": 2,
    "materials": 3,
    "chemical": 2,
    "hardware": 2,
    "applications engineer": 2,
    "systems engineer": 2,
    "supply chain": 2,
    "operations": 1,
    "data analytics": 2,
    "data science": 1,
    "controls": 2,
    "robotics": 2,
    "technician": 2,
}

EXCLUDED_FUNCTION_TERMS = (
    "accounting",
    "finance",
    "human resources",
    "recruiter",
    "communications",
    "public relations",
    "legal",
    "sales intern",
    "marketing intern",
    "product management",
    "product manager",
    "real estate",
)

FLORIDA_TERMS = ("florida", ", fl", "-fl-", "orlando", "melbourne", "tampa", "gainesville")

NON_US_COUNTRY_MARKERS = (
    "argentina",
    "australia",
    "belgium",
    "brazil",
    "bulgaria",
    "canada",
    "china",
    "costa rica",
    "czech republic",
    "czechia",
    "denmark",
    "england",
    "finland",
    "france",
    "germany",
    "hong kong",
    "hungary",
    "singapore",
    "malaysia",
    "india",
    "indonesia",
    "ireland",
    "israel",
    "italy",
    "japan",
    "korea",
    "mexico",
    "netherlands",
    "new zealand",
    "philippines",
    "poland",
    "portugal",
    "romania",
    "scotland",
    "spain",
    "sweden",
    "switzerland",
    "taiwan",
    "thailand",
    "tbilisi",
    "united arab emirates",
    "united kingdom",
    "vietnam",
    "austria",
    "wales",
)

NON_US_COUNTRY_CODES = (
    "kor",
    "sgp",
    "mys",
    "ind",
    "chn",
    "jpn",
    "deu",
    "vnm",
)

US_MARKERS = (
    "united states",
    "usa",
    "u.s.",
    "alabama",
    "alaska",
    "arizona",
    "arkansas",
    "california",
    "colorado",
    "connecticut",
    "delaware",
    "district of columbia",
    "florida",
    "georgia",
    "hawaii",
    "idaho",
    "illinois",
    "indiana",
    "iowa",
    "kansas",
    "kentucky",
    "louisiana",
    "maine",
    "maryland",
    "massachusetts",
    "michigan",
    "minnesota",
    "mississippi",
    "missouri",
    "montana",
    "nebraska",
    "nevada",
    "new hampshire",
    "new jersey",
    "new mexico",
    "new york",
    "north carolina",
    "north dakota",
    "ohio",
    "oklahoma",
    "oregon",
    "pennsylvania",
    "puerto rico",
    "rhode island",
    "south carolina",
    "south dakota",
    "tennessee",
    "texas",
    "utah",
    "vermont",
    "virginia",
    "washington",
    "west virginia",
    "wisconsin",
    "wyoming",
)

US_STATE_CODES = (
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL",
    "GA", "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME",
    "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH",
    "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI",
    "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI",
    "WY", "PR",
)

BACHELOR_PATTERNS = (
    r"\bbachelor(?:'s|s)?\b",
    r"\bundergraduate\b",
    r"\bb\.?\s?s\.?\s+(?:degree|student|candidate)\b",
)

MASTER_PATTERNS = (
    r"\bmaster(?:'s|s)?\b",
    r"\bm\.?\s?s\.?\s+(?:degree|student|candidate)\b",
)

DOCTORAL_TITLE_PATTERNS = (
    r"\bph\.?\s?d\.?\b",
    r"\bdoctoral\b",
    r"\bpostdoc(?:toral)?\b",
)


@dataclass(frozen=True)
class Job:
    company: str
    title: str
    location: str
    url: str
    source: str
    external_id: str = ""
    posted: str = ""
    description: str = ""

    @property
    def stable_id(self) -> str:
        identity = self.external_id.strip() or canonical_url(self.url)
        raw = f"{self.company.casefold()}|{identity.casefold()}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


@dataclass(frozen=True)
class RankedJob:
    job: Job
    score: int
    tags: tuple[str, ...]
    ai_reason: str = ""
    ai_evidence: str = ""


class HttpRequestError(RuntimeError):
    """Raised when a remote job feed or Discord request fails."""


def extract_gemini_text(response: Any) -> str:
    """Extract the first text response from Gemini's generateContent payload."""
    if not isinstance(response, dict):
        raise ValueError("Gemini returned a non-object response")
    candidates = response.get("candidates") or []
    if not candidates:
        feedback = response.get("promptFeedback") or {}
        raise ValueError(f"Gemini returned no candidates: {feedback}")
    parts = ((candidates[0].get("content") or {}).get("parts") or [])
    text = "".join(str(part.get("text", "")) for part in parts if isinstance(part, dict)).strip()
    if not text:
        raise ValueError("Gemini returned no text")
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE | re.DOTALL)
    return text.strip()


def extract_interaction_text(response: Any) -> str:
    """Extract model text from a Gemini Interactions API response."""
    if not isinstance(response, dict):
        raise ValueError("Gemini returned a non-object interaction")
    interaction = response.get("interaction", response)
    if not isinstance(interaction, dict):
        raise ValueError("Gemini returned an invalid interaction")
    if interaction.get("output_text"):
        return str(interaction["output_text"]).strip()
    chunks: list[str] = []
    for step in interaction.get("steps", []):
        if not isinstance(step, dict) or step.get("type") != "model_output":
            continue
        for block in step.get("content", []):
            if isinstance(block, dict) and block.get("type") == "text" and block.get("text"):
                chunks.append(str(block["text"]))
    text = "".join(chunks).strip()
    if not text:
        raise ValueError(f"Gemini interaction returned no model text (status={interaction.get('status')})")
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE | re.DOTALL)
    return text.strip()


def gemini_json(
    api_key: str,
    model: str,
    prompt: str,
    schema: dict[str, Any],
    *,
    use_search: bool = False,
    timeout: int = 90,
) -> dict[str, Any]:
    """Call Gemini with a JSON schema and optionally grounded web tools."""
    if use_search:
        response = request_json(
            "POST",
            GEMINI_INTERACTIONS_URL,
            payload={
                "model": model,
                "input": prompt,
                "tools": [{"type": "google_search"}, {"type": "url_context"}],
                "response_format": {
                    "type": "text",
                    "mime_type": "application/json",
                    "schema": schema,
                },
            },
            headers={"x-goog-api-key": api_key},
            timeout=timeout,
        )
        parsed = json.loads(extract_interaction_text(response))
        if not isinstance(parsed, dict):
            raise ValueError("Gemini JSON response was not an object")
        return parsed

    payload: dict[str, Any] = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseJsonSchema": schema,
        },
    }
    response = request_json(
        "POST",
        f"{GEMINI_API_ROOT}/{model}:generateContent",
        payload=payload,
        headers={"x-goog-api-key": api_key},
        timeout=timeout,
    )
    parsed = json.loads(extract_gemini_text(response))
    if not isinstance(parsed, dict):
        raise ValueError("Gemini JSON response was not an object")
    return parsed


def request_json(
    method: str,
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    params: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 25,
) -> Any:
    if params:
        separator = "&" if "?" in url else "?"
        url = f"{url}{separator}{urlencode(params)}"
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request_headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if payload is not None:
        request_headers["Content-Type"] = "application/json"
    request_headers.update(headers or {})
    request = Request(url, data=body, headers=request_headers, method=method.upper())
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise HttpRequestError(f"HTTP {exc.code} from {url}: {detail}") from exc
    except URLError as exc:
        raise HttpRequestError(f"Request failed for {url}: {exc.reason}") from exc


def request_text(
    method: str,
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    params: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 25,
) -> str:
    """Request a public HTML/XML page without adding a paid API dependency."""
    text, _ = request_text_with_headers(
        method,
        url,
        payload=payload,
        params=params,
        headers=headers,
        timeout=timeout,
    )
    return text


def request_text_with_headers(
    method: str,
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    params: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 25,
) -> tuple[str, Any]:
    if params:
        separator = "&" if "?" in url else "?"
        url = f"{url}{separator}{urlencode(params)}"
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request_headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml,text/xml;q=0.9,*/*;q=0.8",
    }
    if payload is not None:
        request_headers["Content-Type"] = "application/json"
    request_headers.update(headers or {})
    request = Request(url, data=body, headers=request_headers, method=method.upper())
    try:
        with urlopen(request, timeout=timeout) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(charset, errors="replace"), response.headers
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise HttpRequestError(f"HTTP {exc.code} from {url}: {detail}") from exc
    except URLError as exc:
        raise HttpRequestError(f"Request failed for {url}: {exc.reason}") from exc


def response_cookie_header(response_headers: Any) -> str:
    """Return response cookies in the compact form expected by later API calls."""
    raw_cookies: list[str] = []
    if hasattr(response_headers, "get_all"):
        raw_cookies = list(response_headers.get_all("Set-Cookie") or [])
    elif hasattr(response_headers, "get"):
        raw_value = response_headers.get("Set-Cookie")
        if isinstance(raw_value, list):
            raw_cookies = [str(value) for value in raw_value]
        elif raw_value:
            raw_cookies = [str(raw_value)]

    cookie_pairs: dict[str, str] = {}
    for raw_cookie in raw_cookies:
        pair = raw_cookie.split(";", 1)[0].strip()
        if "=" not in pair:
            continue
        name, value = pair.split("=", 1)
        if name.strip():
            cookie_pairs[name.strip()] = value.strip()
    return "; ".join(f"{name}={value}" for name, value in cookie_pairs.items())


def canonical_url(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{parsed.path.rstrip('/')}"


def clean_text(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def matches_any(text: str, patterns: Iterable[str]) -> bool:
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


def target_role_tags(title: str) -> tuple[str, ...]:
    """Return qualifying semiconductor-function tags found in a job title."""
    return tuple(
        tag for pattern, tag in TARGET_ROLE_PATTERNS if re.search(pattern, title, re.IGNORECASE)
    )


def matches_target_role(title: str) -> bool:
    return bool(target_role_tags(title))


def contains_country_code(text: str, codes: Iterable[str]) -> bool:
    code_pattern = "|".join(re.escape(code) for code in codes)
    return bool(re.search(rf"(?:^|[\s,;/()_-])(?:{code_pattern})(?:$|[\s,;/()_-])", text, re.IGNORECASE))


def contains_phrase(text: str, phrases: Iterable[str]) -> bool:
    cleaned = clean_text(text).casefold()
    return any(
        re.search(rf"(?<![a-z0-9]){re.escape(phrase.casefold())}(?![a-z0-9])", cleaned)
        for phrase in phrases
    )


def has_non_us_marker(text: str) -> bool:
    return contains_phrase(text, NON_US_COUNTRY_MARKERS) or contains_country_code(
        text, NON_US_COUNTRY_CODES
    )


def has_explicit_us_location(location: str) -> bool:
    cleaned = clean_text(location)
    if not cleaned or has_non_us_marker(cleaned):
        return False
    if contains_phrase(cleaned, US_MARKERS):
        return True
    if contains_country_code(cleaned, ("US",)):
        return True
    return contains_country_code(cleaned, US_STATE_CODES)


def is_us_based(title: str, location: str) -> bool:
    if has_non_us_marker(f"{title} {location}"):
        return False
    return has_explicit_us_location(location)


def looks_non_us(location: str) -> bool:
    """Return true for foreign or insufficiently specific locations."""
    return not has_explicit_us_location(location)


def score_job(job: Job, us_only: bool = True) -> RankedJob | None:
    title = clean_text(job.title)
    combined = f"{title} {clean_text(job.description)[:2500]}".casefold()
    title_lower = title.casefold()

    if not matches_any(title, INTERNSHIP_PATTERNS):
        return None
    role_tags = target_role_tags(title)
    if not role_tags:
        return None
    if any(term in title_lower for term in EXCLUDED_FUNCTION_TERMS):
        return None
    if matches_any(title, SENIOR_PATTERNS) and "intern" not in title_lower:
        return None
    if matches_any(title, DOCTORAL_TITLE_PATTERNS) and not (
        matches_any(title, BACHELOR_PATTERNS) or matches_any(title, MASTER_PATTERNS)
    ):
        return None
    if us_only and not is_us_based(title, job.location):
        return None

    # Every title that reaches this point satisfies both hard eligibility gates.
    # Seven is therefore the minimum qualifying score before preference boosts.
    score = 7
    tags: list[str] = [opportunity_type(title), *role_tags]

    if matches_any(combined, BACHELOR_PATTERNS):
        score += 4
        tags.append("Bachelor's")
    if matches_any(combined, MASTER_PATTERNS):
        score += 4
        tags.append("Master's")

    for term, points in HIGH_PRIORITY_TERMS.items():
        if term in combined:
            score += points
            tag = tag_for_term(term)
            if tag not in tags:
                tags.append(tag)

    for term, points in MEDIUM_PRIORITY_TERMS.items():
        if term in combined:
            score += points
            tag = tag_for_term(term)
            if tag not in tags:
                tags.append(tag)

    if any(term in job.location.casefold() for term in FLORIDA_TERMS):
        score += 3
        tags.append("Florida")

    if score < 7:
        return None
    return RankedJob(job=job, score=score, tags=tuple(tags[:5]))


def rank_jobs(jobs: Iterable[Job], us_only: bool = True) -> list[RankedJob]:
    ranked: list[RankedJob] = []
    for job in jobs:
        result = score_job(job, us_only=us_only)
        if result is not None:
            ranked.append(result)
    ranked.sort(key=lambda item: (-item.score, item.job.company.casefold(), item.job.title.casefold()))
    return ranked


AI_ROLE_CATEGORIES = (
    "Process", "Yield", "Manufacturing", "Product", "Equipment", "Metrology",
    "Integration", "Lithography", "Etch", "Deposition", "CVD", "PVD", "ALD",
    "CMP", "Packaging", "Test", "Reliability", "Semiconductor",
)


def eligible_for_ai_review(job: Job, us_only: bool = True) -> bool:
    """Apply only objective gates before asking AI to assess technical relevance."""
    title = clean_text(job.title)
    title_lower = title.casefold()
    if not matches_any(title, INTERNSHIP_PATTERNS):
        return False
    if any(term in title_lower for term in EXCLUDED_FUNCTION_TERMS):
        return False
    if matches_any(title, SENIOR_PATTERNS) and "intern" not in title_lower:
        return False
    if matches_any(title, DOCTORAL_TITLE_PATTERNS) and not (
        matches_any(title, BACHELOR_PATTERNS) or matches_any(title, MASTER_PATTERNS)
    ):
        return False
    return not us_only or is_us_based(title, job.location)


def review_jobs_with_gemini(
    jobs: Iterable[Job],
    *,
    api_key: str,
    model: str = DEFAULT_GEMINI_MODEL,
    minimum_confidence: int = 72,
    batch_size: int = 12,
) -> list[RankedJob]:
    """Use Gemini as a semantic verifier and ranker for collected openings."""
    candidates = [job for job in jobs if eligible_for_ai_review(job, us_only=True)]
    schema = {
        "type": "object",
        "properties": {
            "decisions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "index": {"type": "integer"},
                        "accept": {"type": "boolean"},
                        "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
                        "us_based": {"type": "boolean"},
                        "internship_or_coop": {"type": "boolean"},
                        "categories": {
                            "type": "array",
                            "items": {"type": "string", "enum": list(AI_ROLE_CATEGORIES)},
                        },
                        "degrees": {
                            "type": "array",
                            "items": {"type": "string", "enum": ["Bachelor's", "Master's", "Unspecified"]},
                        },
                        "reason": {"type": "string"},
                        "evidence": {"type": "string"},
                    },
                    "required": [
                        "index", "accept", "confidence", "us_based", "internship_or_coop",
                        "categories", "degrees", "reason", "evidence",
                    ],
                },
            }
        },
        "required": ["decisions"],
    }
    ranked: list[RankedJob] = []
    for offset in range(0, len(candidates), max(1, batch_size)):
        batch = candidates[offset : offset + max(1, batch_size)]
        records = [
            {
                "index": index,
                "company": job.company,
                "title": job.title,
                "location": job.location,
                "description": clean_text(job.description)[:7000],
            }
            for index, job in enumerate(batch)
        ]
        prompt = (
            "You are the independent screening agent for a U.S. semiconductor-manufacturing "
            "student organization. Review every supplied opening. Accept only an internship or "
            "co-op that is explicitly U.S.-based and substantially involves at least one of: "
            "semiconductor process engineering, yield, manufacturing, product engineering, fab "
            "equipment, metrology, process integration, lithography, etch, thin-film deposition, "
            "CVD, PVD, ALD, CMP, semiconductor packaging, semiconductor test, or reliability. "
            "Judge the actual duties, not just keyword presence. Reject software-only, business, "
            "finance, marketing, sales, supply-chain-only, generic circuit-design, and unrelated "
            "laboratory roles. A generic title such as 'Engineering Intern' may be accepted when "
            "its description clearly establishes relevant manufacturing work. Bachelor's and "
            "master's eligibility are preferences, not mandatory gates. Set confidence to your "
            "0-100 relevance confidence. Give a concise paraphrased reason and concise supporting "
            "evidence from the supplied record. Do not invent missing facts. Return one decision "
            "for every index.\n\nOPENINGS:\n" + json.dumps(records, ensure_ascii=False)
        )
        response = gemini_json(api_key, model, prompt, schema)
        decisions = response.get("decisions") or []
        decision_by_index = {
            item.get("index"): item for item in decisions if isinstance(item, dict)
        }
        for index, job in enumerate(batch):
            decision = decision_by_index.get(index)
            if not decision:
                continue
            confidence = max(0, min(100, int(decision.get("confidence", 0) or 0)))
            if not (
                decision.get("accept")
                and decision.get("us_based")
                and decision.get("internship_or_coop")
                and confidence >= minimum_confidence
            ):
                continue
            categories = [
                value for value in decision.get("categories", []) if value in AI_ROLE_CATEGORIES
            ]
            degrees = [
                value for value in decision.get("degrees", [])
                if value in {"Bachelor's", "Master's"}
            ]
            tags = [opportunity_type(job.title), *categories, *degrees]
            if any(term in job.location.casefold() for term in FLORIDA_TERMS):
                tags.append("Florida")
            tags = list(dict.fromkeys(tags))
            preference_boost = 4 * len(degrees) + (3 if "Florida" in tags else 0)
            ranked.append(
                RankedJob(
                    job=job,
                    score=confidence + preference_boost,
                    tags=tuple(tags[:6]),
                    ai_reason=clean_text(decision.get("reason"))[:300],
                    ai_evidence=clean_text(decision.get("evidence"))[:300],
                )
            )
    ranked.sort(key=lambda item: (-item.score, item.job.company.casefold(), item.job.title.casefold()))
    return ranked


def source_hosts(source: dict[str, Any]) -> set[str]:
    """Return configured official/ATS hosts accepted for grounded discoveries."""
    hosts: set[str] = set()
    for key in ("host", "domain", "feed_url", "page_url"):
        value = clean_text(source.get(key))
        if not value:
            continue
        parsed = urlparse(value if "://" in value else f"https://{value}")
        if parsed.hostname:
            hosts.add(parsed.hostname.casefold().removeprefix("www."))
    if source.get("type") == "greenhouse":
        hosts.update({"boards.greenhouse.io", "job-boards.greenhouse.io"})
    elif source.get("type") == "adp_myjobs":
        hosts.add("myjobs.adp.com")
    elif source.get("type") == "adp_workforcenow":
        hosts.add("workforcenow.adp.com")
    return hosts


def url_matches_source(url: str, source: dict[str, Any]) -> bool:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        return False
    candidate = parsed.hostname.casefold().removeprefix("www.")
    return any(candidate == host or candidate.endswith(f".{host}") for host in source_hosts(source))


def discover_jobs_with_gemini(
    sources: Iterable[dict[str, Any]],
    *,
    api_key: str,
    model: str = DEFAULT_GEMINI_DISCOVERY_MODEL,
    companies_per_query: int = 6,
) -> list[Job]:
    """Find additional live roles using grounded Search plus URL Context."""
    enabled = [source for source in sources if source.get("enabled", True)]
    source_by_company = {source["company"].casefold(): source for source in enabled}
    schema = {
        "type": "object",
        "properties": {
            "jobs": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "company": {"type": "string"},
                        "title": {"type": "string"},
                        "location": {"type": "string"},
                        "url": {"type": "string"},
                        "posted": {"type": "string"},
                        "description": {"type": "string"},
                        "evidence": {"type": "string"},
                        "verified_live": {"type": "boolean"},
                    },
                    "required": [
                        "company", "title", "location", "url", "posted", "description",
                        "evidence", "verified_live",
                    ],
                },
            }
        },
        "required": ["jobs"],
    }
    found: dict[str, Job] = {}
    chunk_size = max(1, companies_per_query)
    for offset in range(0, len(enabled), chunk_size):
        group = enabled[offset : offset + chunk_size]
        targets = [
            {
                "company": source["company"],
                "allowed_hosts": sorted(source_hosts(source)),
            }
            for source in group
        ]
        prompt = (
            "Act as a job-discovery agent. Use Google Search and URL Context to find currently "
            "live U.S.-based internships and co-ops for the listed semiconductor companies. "
            "Prioritize process, yield, manufacturing, product engineering, equipment, metrology, "
            "integration, lithography, etch, deposition, CVD, PVD, ALD, CMP, packaging, test, "
            "reliability, and other genuinely semiconductor-manufacturing duties. Search beyond "
            "exact title keywords. Return only individual official company/ATS job pages whose "
            "current page you verified with URL Context. Do not return aggregators, search-result "
            "pages, expired listings, general career pages, non-U.S. roles, full-time roles, or "
            "invented URLs. The URL host must be one of the allowed hosts for that company. "
            "Description and evidence must be concise paraphrases of the live page. If no verified "
            "role exists, return none for that company.\n\nTARGETS:\n" + json.dumps(targets)
        )
        response = gemini_json(api_key, model, prompt, schema, use_search=True, timeout=120)
        for item in response.get("jobs", []):
            if not isinstance(item, dict) or not item.get("verified_live"):
                continue
            source = source_by_company.get(clean_text(item.get("company")).casefold())
            if not source:
                continue
            title = clean_text(item.get("title"))
            location = clean_text(item.get("location"))
            url = clean_text(item.get("url"))
            if not matches_any(title, INTERNSHIP_PATTERNS):
                continue
            if not is_us_based(title, location) or not url_matches_source(url, source):
                continue
            job = Job(
                company=source["company"],
                title=title,
                location=location,
                url=url,
                source="Gemini grounded search",
                posted=clean_text(item.get("posted")),
                description=" ".join(
                    filter(None, [clean_text(item.get("description")), clean_text(item.get("evidence"))])
                ),
            )
            found[job.stable_id] = job
    return list(found.values())


def deduplicate_jobs(jobs: Iterable[Job]) -> list[Job]:
    """Merge ATS and grounded-search copies while preferring richer ATS records."""
    selected: dict[tuple[str, str, str], Job] = {}
    for job in jobs:
        key = (
            job.company.casefold(),
            clean_text(job.title).casefold(),
            clean_text(job.location).casefold(),
        )
        current = selected.get(key)
        if current is None:
            selected[key] = job
            continue
        current_quality = (current.source != "Gemini grounded search", len(current.description))
        job_quality = (job.source != "Gemini grounded search", len(job.description))
        if job_quality > current_quality:
            selected[key] = job
    return list(selected.values())


def opportunity_type(title: str) -> str:
    lowered = title.casefold()
    if "co-op" in lowered or "coop" in lowered or "co op" in lowered:
        return "Co-op"
    return "Internship"


def tag_for_term(term: str) -> str:
    mapping = {
        "process integration": "Process Integration",
        "process": "Process",
        "yield": "Yield",
        "manufacturing": "Manufacturing",
        "fabrication": "Fab",
        "fab ": "Fab",
        "equipment": "Equipment",
        "field service": "Field Service",
        "metrology": "Metrology",
        "defect": "Defect",
        "failure analysis": "Failure Analysis",
        "reliability": "Reliability",
        "product engineer": "Product",
        "test engineer": "Test",
        "quality engineer": "Quality",
        "industrial engineer": "Industrial",
        "semiconductor": "Semiconductor",
        "thin film": "Thin Films",
        "deposition": "Deposition",
        "lithography": "Lithography",
        "etch": "Etch",
        "cmp": "CMP",
        "diffusion": "Diffusion",
        "implant": "Implant",
        "epitaxy": "Epitaxy",
        "packaging": "Packaging",
        "cleanroom": "Cleanroom",
        "vacuum": "Vacuum",
        "process control": "Process Control",
        "automation": "Automation",
        "facilities": "Facilities",
        "supply chain": "Supply Chain",
        "data analytics": "Data Analytics",
        "data science": "Data Science",
    }
    return mapping.get(term, term.title())


def first_clean(mapping: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = mapping.get(key)
        if value is not None and clean_text(value):
            return clean_text(value)
    return ""


def xml_text(element: ET.Element, *names: str) -> str:
    wanted = {name.casefold() for name in names}
    for child in element.iter():
        local_name = child.tag.rsplit("}", 1)[-1].casefold()
        if local_name in wanted and child.text and child.text.strip():
            return clean_text(child.text)
    return ""


def split_trailing_location(title: str) -> tuple[str, str]:
    match = re.match(r"^(?P<title>.+?)\s*\((?P<location>[^()]+)\)\s*$", title)
    if not match:
        return title, ""
    location = clean_text(match.group("location"))
    if "," not in location and not has_explicit_us_location(location) and not has_non_us_marker(location):
        return title, ""
    return clean_text(match.group("title")), location


def structured_location(item: dict[str, Any], fields: Iterable[str]) -> str:
    values: list[str] = []
    for field in fields:
        raw = item.get(field)
        entries = raw if isinstance(raw, list) else ([raw] if raw else [])
        for entry in entries:
            if isinstance(entry, str):
                label = clean_text(entry)
            elif isinstance(entry, dict):
                name_code = entry.get("nameCode") if isinstance(entry.get("nameCode"), dict) else {}
                address = entry.get("address") if isinstance(entry.get("address"), dict) else entry
                state_data = (
                    address.get("countrySubdivisionLevel1")
                    if isinstance(address.get("countrySubdivisionLevel1"), dict)
                    else {}
                )
                country_data = address.get("country") if isinstance(address.get("country"), dict) else {}
                parts = [
                    first_clean(entry, "displayName", "locationName", "name"),
                    first_clean(name_code, "shortName", "longName"),
                    first_clean(address, "cityName", "city"),
                    first_clean(state_data, "codeValue", "longName"),
                    first_clean(address, "state", "province"),
                    first_clean(country_data, "codeValue", "longName"),
                    first_clean(address, "country"),
                ]
                label = ", ".join(part for part in parts if part)
            else:
                label = ""
            if label and label.casefold() not in {value.casefold() for value in values}:
                values.append(label)
    return " / ".join(values)


class Scanner:
    def __init__(self, timeout: int = 25) -> None:
        self.timeout = timeout

    def scan(self, source: dict[str, Any]) -> list[Job]:
        source_type = source.get("type", "").casefold()
        if source_type == "workday":
            return self._workday(source)
        if source_type == "greenhouse":
            return self._greenhouse(source)
        if source_type == "lever":
            return self._lever(source)
        if source_type == "ashby":
            return self._ashby(source)
        if source_type == "eightfold":
            return self._eightfold(source)
        if source_type == "oracle":
            return self._oracle(source)
        if source_type == "successfactors":
            return self._successfactors(source)
        if source_type == "dayforce":
            return self._dayforce(source)
        if source_type == "icims":
            return self._icims(source)
        if source_type == "adp_workforcenow":
            return self._adp_workforcenow(source)
        if source_type == "adp_myjobs":
            return self._adp_myjobs(source)
        if source_type == "csod":
            return self._csod(source)
        if source_type == "static":
            return self._static(source)
        raise ValueError(f"Unsupported source type: {source_type!r}")

    def _workday(self, source: dict[str, Any]) -> list[Job]:
        host = source["host"].strip().rstrip("/")
        tenant = source["tenant"]
        site = source["site"]
        endpoint = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
        terms = source.get("search_terms", ["intern", "co-op"])
        jobs: dict[str, Job] = {}
        external_paths: dict[str, str] = {}

        for term in terms:
            offset = 0
            while offset < int(source.get("max_results_per_term", 80)):
                payload = {
                    "appliedFacets": {},
                    "limit": 20,
                    "offset": offset,
                    "searchText": term,
                }
                data = request_json(
                    "POST",
                    endpoint,
                    payload=payload,
                    timeout=self.timeout,
                )
                postings = data.get("jobPostings", [])
                if not postings:
                    break
                for item in postings:
                    external_path = item.get("externalPath", "")
                    url = (
                        external_path
                        if external_path.startswith("http")
                        else f"https://{host}/en-US/{site}{external_path}"
                    )
                    job = Job(
                        company=source["company"],
                        title=clean_text(item.get("title")),
                        location=clean_text(item.get("locationsText") or item.get("location")),
                        url=url,
                        source="Workday",
                        external_id=clean_text(item.get("bulletFields", [""])[0] if item.get("bulletFields") else external_path),
                        posted=clean_text(item.get("postedOn")),
                    )
                    jobs[job.stable_id] = job
                    external_paths[job.stable_id] = external_path
                offset += len(postings)
                if offset >= int(data.get("total", offset)):
                    break

        if source.get("fetch_details", True):
            candidates = [
                job
                for job in jobs.values()
                if matches_any(job.title, INTERNSHIP_PATTERNS)
                and not has_non_us_marker(f"{job.title} {job.location}")
            ]
            candidates.sort(
                key=lambda job: (job.company.casefold(), job.title.casefold()),
            )
            detail_limit = int(source.get("max_detail_requests", 50))
            for job in candidates[:detail_limit]:
                external_path = external_paths.get(job.stable_id, "")
                if not external_path or external_path.startswith("http"):
                    continue
                detail_url = f"https://{host}/wday/cxs/{tenant}/{site}{external_path}"
                try:
                    detail = request_json("GET", detail_url, timeout=self.timeout)
                    info = detail.get("jobPostingInfo", {})
                    jobs[job.stable_id] = Job(
                        company=job.company,
                        title=clean_text(info.get("title")) or job.title,
                        location=clean_text(info.get("location")) or job.location,
                        url=clean_text(info.get("externalUrl")) or job.url,
                        source=job.source,
                        external_id=job.external_id,
                        posted=clean_text(info.get("startDate")) or job.posted,
                        description=clean_text(info.get("jobDescription")),
                    )
                except (HttpRequestError, json.JSONDecodeError):
                    # Search results remain usable when a detail endpoint is unavailable.
                    continue
        return list(jobs.values())

    def _greenhouse(self, source: dict[str, Any]) -> list[Job]:
        board = source["board_token"]
        url = f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"
        data = request_json("GET", url, timeout=self.timeout)
        jobs: list[Job] = []
        for item in data.get("jobs", []):
            location = clean_text((item.get("location") or {}).get("name"))
            departments = " ".join(clean_text(x.get("name")) for x in item.get("departments", []))
            jobs.append(
                Job(
                    company=source["company"],
                    title=clean_text(item.get("title")),
                    location=location,
                    url=item.get("absolute_url", ""),
                    source="Greenhouse",
                    external_id=str(item.get("id", "")),
                    posted=clean_text(item.get("updated_at")),
                    description=f"{departments} {clean_text(item.get('content'))}",
                )
            )
        return jobs

    def _lever(self, source: dict[str, Any]) -> list[Job]:
        site = source["site"]
        url = f"https://api.lever.co/v0/postings/{site}?mode=json"
        data = request_json("GET", url, timeout=self.timeout)
        jobs: list[Job] = []
        for item in data:
            categories = item.get("categories", {})
            location = clean_text(categories.get("location") or item.get("workplaceType"))
            description = " ".join(
                [
                    clean_text(categories.get("team")),
                    clean_text(categories.get("department")),
                    clean_text(item.get("descriptionPlain")),
                ]
            )
            jobs.append(
                Job(
                    company=source["company"],
                    title=clean_text(item.get("text")),
                    location=location,
                    url=item.get("hostedUrl", ""),
                    source="Lever",
                    external_id=str(item.get("id", "")),
                    description=description,
                )
            )
        return jobs

    def _ashby(self, source: dict[str, Any]) -> list[Job]:
        board = source["board_name"]
        url = f"https://api.ashbyhq.com/posting-api/job-board/{board}"
        data = request_json(
            "GET",
            url,
            params={"includeCompensation": "false"},
            timeout=self.timeout,
        )
        jobs: list[Job] = []
        for item in data.get("jobs", []):
            jobs.append(
                Job(
                    company=source["company"],
                    title=clean_text(item.get("title")),
                    location=clean_text(item.get("location") or item.get("workplaceType")),
                    url=item.get("jobUrl") or item.get("applyUrl") or "",
                    source="Ashby",
                    external_id=str(item.get("id", "")),
                    posted=clean_text(item.get("publishedAt")),
                    description=clean_text(item.get("descriptionPlain")),
                )
            )
        return jobs

    def _eightfold(self, source: dict[str, Any]) -> list[Job]:
        host = source["host"].strip().rstrip("/")
        domain = source.get("domain", "").strip()
        terms = source.get("search_terms", ["intern", "co-op"])
        max_pages = int(source.get("max_pages_per_term", 30))
        jobs: dict[str, Job] = {}
        position_ids: dict[str, str] = {}
        configured_generation = source.get("api_generation", "auto").strip().casefold()
        if configured_generation not in {"auto", "pcsx", "classic"}:
            raise ValueError(f"Unsupported Eightfold API generation: {configured_generation!r}")
        generation = configured_generation

        def position_location(item: dict[str, Any]) -> str:
            values: list[str] = []

            def add_location(raw: Any) -> None:
                if isinstance(raw, list):
                    for entry in raw:
                        add_location(entry)
                    return
                if isinstance(raw, str):
                    label = clean_text(raw)
                elif isinstance(raw, dict):
                    label = first_clean(
                        raw,
                        "displayName",
                        "formattedAddress",
                        "locationName",
                        "name",
                        "location",
                    )
                    if not label:
                        label = ", ".join(
                            filter(
                                None,
                                [
                                    first_clean(raw, "city", "cityName"),
                                    first_clean(raw, "state", "stateName", "region"),
                                    first_clean(raw, "country", "countryName"),
                                ],
                            )
                        )
                else:
                    label = ""
                if label and label.casefold() not in {value.casefold() for value in values}:
                    values.append(label)

            for key in (
                "location",
                "primaryLocation",
                "standardizedLocations",
                "locations",
                "standardized_locations",
            ):
                add_location(item.get(key))
            return " / ".join(values)

        def position_posted(item: dict[str, Any]) -> str:
            raw = (
                item.get("postedTs")
                or item.get("creationTs")
                or item.get("t_create")
                or item.get("t_update")
            )
            if raw in (None, ""):
                return first_clean(item, "postedDate", "posted_date", "datePosted")
            try:
                timestamp = float(raw)
                if timestamp > 10_000_000_000:
                    timestamp /= 1000
                return datetime.fromtimestamp(timestamp, timezone.utc).date().isoformat()
            except (TypeError, ValueError, OSError):
                return clean_text(raw)

        def position_url(item: dict[str, Any], external_id: str, api: str) -> str:
            raw_url = first_clean(
                item,
                "positionUrl",
                "publicUrl",
                "canonicalPositionUrl",
                "position_url",
                "public_url",
                "url",
            )
            if raw_url.startswith("http://") or raw_url.startswith("https://"):
                return raw_url
            if raw_url and not raw_url.casefold().startswith("javascript:"):
                return urljoin(f"https://{host}/", raw_url)
            if not external_id:
                return ""
            if api == "pcsx":
                suffix = f"?{urlencode({'domain': domain})}" if domain else ""
                return f"https://{host}/careers/job/{external_id}{suffix}"
            query = {"pid": external_id}
            if domain:
                query["domain"] = domain
            return f"https://{host}/careers?{urlencode(query)}"

        def normalize_position(
            item: dict[str, Any], api: str, fallback: Job | None = None
        ) -> Job:
            found_id = first_clean(
                item,
                "id",
                "positionId",
                "position_id",
                "displayJobId",
                "display_job_id",
                "atsJobId",
                "requisitionId",
            )
            external_id = fallback.external_id if fallback and fallback.external_id else found_id
            description = " ".join(
                filter(
                    None,
                    [
                        first_clean(item, "department"),
                        first_clean(item, "businessUnit", "business_unit"),
                        first_clean(item, "workLocationOption"),
                        first_clean(item, "jobDescription", "job_description", "description"),
                    ],
                )
            )
            if fallback and fallback.description and fallback.description not in description:
                description = " ".join(filter(None, [description, fallback.description]))
            return Job(
                company=source["company"],
                title=first_clean(item, "name", "title", "posting_name")
                or (fallback.title if fallback else ""),
                location=position_location(item) or (fallback.location if fallback else ""),
                url=position_url(item, external_id or found_id, api)
                or (fallback.url if fallback else ""),
                source="Eightfold",
                external_id=external_id,
                posted=position_posted(item) or (fallback.posted if fallback else ""),
                description=description,
            )

        for term in terms:
            for page in range(max_pages):
                start = page * 10
                wrapper: dict[str, Any] = {}
                if generation in {"auto", "pcsx"}:
                    params = {
                        "query": term,
                        "location": "",
                        "start": str(start),
                        "sort_by": "most_recent",
                        "filter_include_remote": "1",
                    }
                    if domain:
                        params["domain"] = domain
                    try:
                        data = request_json(
                            "GET",
                            f"https://{host}/api/pcsx/search",
                            params=params,
                            headers={
                                "Origin": f"https://{host}",
                                "Referer": f"https://{host}/careers",
                            },
                            timeout=self.timeout,
                        )
                        pcsx_wrapper = data.get("data") if isinstance(data, dict) else None
                        if isinstance(pcsx_wrapper, dict) and (
                            "positions" in pcsx_wrapper or "count" in pcsx_wrapper
                        ):
                            generation = "pcsx"
                            wrapper = pcsx_wrapper
                        elif generation == "pcsx":
                            raise HttpRequestError(
                                f"Eightfold PCSX returned an unexpected response for {host}"
                            )
                        else:
                            generation = "classic"
                    except (HttpRequestError, json.JSONDecodeError):
                        if generation == "pcsx":
                            raise
                        generation = "classic"

                if generation == "classic":
                    params = {
                        "query": term,
                        "start": str(start),
                        "num": "10",
                        "sort_by": "timestamp",
                    }
                    if domain:
                        params["domain"] = domain
                    data = request_json(
                        "GET",
                        f"https://{host}/api/apply/v2/jobs",
                        params=params,
                        headers={"Referer": f"https://{host}/careers"},
                        timeout=self.timeout,
                    )
                    wrapper = data if isinstance(data, dict) else {}

                positions = wrapper.get("positions", [])
                if not isinstance(positions, list) or not positions:
                    break
                for item in positions:
                    if not isinstance(item, dict):
                        continue
                    job = normalize_position(item, generation)
                    if job.url and job.title:
                        jobs[job.stable_id] = job
                        position_ids[job.stable_id] = first_clean(
                            item, "id", "positionId", "position_id"
                        ) or job.external_id
                count = int(wrapper.get("count", 0) or 0)
                if len(positions) < 10 or (
                    generation == "pcsx" and count and start + len(positions) >= count
                ):
                    break

        if generation == "pcsx" and source.get("fetch_details", True):
            candidates = [
                job
                for job in jobs.values()
                if matches_any(job.title, INTERNSHIP_PATTERNS)
                and not has_non_us_marker(f"{job.title} {job.location}")
            ]
            candidates.sort(
                key=lambda job: (job.company.casefold(), job.title.casefold()),
            )
            for job in candidates[: int(source.get("max_detail_requests", 50))]:
                position_id = position_ids.get(job.stable_id, "")
                if not position_id:
                    continue
                params = {"position_id": position_id, "hl": "en"}
                if domain:
                    params["domain"] = domain
                try:
                    detail = request_json(
                        "GET",
                        f"https://{host}/api/pcsx/position_details",
                        params=params,
                        headers={"Referer": job.url or f"https://{host}/careers"},
                        timeout=self.timeout,
                    )
                except (HttpRequestError, json.JSONDecodeError):
                    continue
                detail_wrapper = detail.get("data") if isinstance(detail, dict) else None
                if not isinstance(detail_wrapper, dict):
                    continue
                position = detail_wrapper.get("position", detail_wrapper)
                if isinstance(position, dict):
                    jobs[job.stable_id] = normalize_position(position, "pcsx", fallback=job)
        return list(jobs.values())

    def _oracle(self, source: dict[str, Any]) -> list[Job]:
        host = source["host"].strip().rstrip("/")
        site = source["site"]
        endpoint = f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
        terms = source.get("search_terms", ["intern", "co-op"])
        limit = min(100, int(source.get("page_size", 100)))
        max_results = int(source.get("max_results_per_term", 200))
        jobs: dict[str, Job] = {}

        for term in terms:
            offset = 0
            while offset < max_results:
                finder = (
                    f"findReqs;siteNumber={site},keyword={term},"
                    "facetsList=LOCATIONS;WORK_LOCATIONS;WORKPLACE_TYPES;TITLES;"
                    "CATEGORIES;ORGANIZATIONS;POSTING_DATES;FLEX_FIELDS,"
                    f"limit={limit},offset={offset},sortBy=POSTING_DATES_DESC"
                )
                data = request_json(
                    "GET",
                    endpoint,
                    params={
                        "onlyData": "true",
                        "expand": (
                            "requisitionList.workLocation,requisitionList.otherWorkLocations,"
                            "requisitionList.secondaryLocations,requisitionList.requisitionFlexFields"
                        ),
                        "finder": finder,
                    },
                    timeout=self.timeout,
                )
                wrapper = (data.get("items") or [{}])[0] if isinstance(data, dict) else {}
                postings = wrapper.get("requisitionList", []) if isinstance(wrapper, dict) else []
                if not isinstance(postings, list) or not postings:
                    break
                for item in postings:
                    if not isinstance(item, dict):
                        continue
                    external_id = first_clean(item, "Id", "id", "RequisitionNumber")
                    direct_url = first_clean(item, "ExternalUrl", "externalUrl", "ExternalUrlSeo")
                    url = direct_url if direct_url.startswith("http") else (
                        f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{external_id}/"
                        if external_id
                        else ""
                    )
                    location = first_clean(item, "PrimaryLocation", "primaryLocation", "Location")
                    if not location:
                        location = structured_location(
                            item,
                            ("workLocation", "otherWorkLocations", "secondaryLocations"),
                        )
                    job = Job(
                        company=source["company"],
                        title=first_clean(item, "Title", "title"),
                        location=location,
                        url=url,
                        source="Oracle Recruiting",
                        external_id=external_id,
                        posted=first_clean(item, "PostedDate", "postedDate"),
                        description=" ".join(
                            filter(
                                None,
                                [
                                    first_clean(item, "ShortDescriptionStr", "shortDescription"),
                                    first_clean(item, "ExternalDescriptionStr", "externalDescription"),
                                    first_clean(item, "Category", "Organization"),
                                ],
                            )
                        ),
                    )
                    if job.url and job.title:
                        jobs[job.stable_id] = job
                offset += len(postings)
                total = int(wrapper.get("TotalJobsCount", offset) or offset)
                if len(postings) < limit or offset >= total:
                    break
        return list(jobs.values())

    def _successfactors(self, source: dict[str, Any]) -> list[Job]:
        if source.get("legacy_company_id"):
            host = source["host"].strip().rstrip("/")
            company_id = source["legacy_company_id"]
            feed_url = f"https://{host}/career"
            xml_source = request_text(
                "GET",
                feed_url,
                params={
                    "company": company_id,
                    "career_ns": "job_listing_summary",
                    "resultType": "XML",
                    "rcm_site_locale": source.get("locale", "en_US"),
                },
                timeout=self.timeout,
            )
        else:
            feed_url = source["feed_url"]
            company_id = ""
            xml_source = request_text("GET", feed_url, timeout=self.timeout)

        try:
            root = ET.fromstring(xml_source)
        except ET.ParseError as exc:
            raise HttpRequestError(f"Malformed SuccessFactors XML from {feed_url}: {exc}") from exc

        jobs: list[Job] = []
        is_legacy = root.tag.rsplit("}", 1)[-1].casefold() == "job-listing"
        items = list(root.findall("./Job")) if is_legacy else list(root.iter("item"))
        for item in items:
            if is_legacy:
                external_id = xml_text(item, "ReqId")
                title, title_location = split_trailing_location(xml_text(item, "JobTitle"))
                location = xml_text(item, "Location") or title_location
                if not location:
                    labeled_values: dict[str, str] = {}
                    for child in item:
                        label = xml_text(child, "label")
                        value = xml_text(child, "value")
                        if label and value:
                            labeled_values[label.casefold()] = value
                    location = next(
                        (value for label, value in labeled_values.items() if "location" in label),
                        "",
                    )
                url = (
                    f"https://{source['host']}/sfcareer/jobreqcareer?"
                    f"{urlencode({'jobId': external_id, 'company': company_id})}"
                )
                posted = xml_text(item, "Posted-Date")
                description = " ".join(clean_text(child.text) for child in item if child.text)
            else:
                title, title_location = split_trailing_location(xml_text(item, "title"))
                url = xml_text(item, "link")
                external_id = xml_text(item, "id", "guid") or canonical_url(url)
                location = xml_text(item, "location") or title_location
                posted = xml_text(item, "pubDate", "postDate", "publishedDate", "date")
                description = xml_text(item, "description", "job_function")
            if title and url:
                jobs.append(
                    Job(
                        company=source["company"],
                        title=title,
                        location=location,
                        url=url,
                        source="SuccessFactors",
                        external_id=external_id,
                        posted=posted,
                        description=description,
                    )
                )
        return jobs

    def _dayforce(self, source: dict[str, Any]) -> list[Job]:
        host = source.get("host", "jobs.dayforcehcm.com").strip().rstrip("/")
        client = source["client"]
        board = source.get("board", "CANDIDATEPORTAL")
        culture = source.get("culture", "en-US")
        endpoint = f"https://{host}/api/geo/{client}/jobposting/search"
        page_size = 25
        max_pages = int(source.get("max_pages", 10))
        jobs: dict[str, Job] = {}

        context_url = (
            f"https://{host}/api/geo/{client}/sitecontext/"
            f"{client}/{board}/{culture}"
        )
        try:
            context = request_json("GET", context_url, timeout=self.timeout)
            context_data = context.get("siteContext", context) if isinstance(context, dict) else {}
            if isinstance(context_data, dict):
                board = first_clean(context_data, "jobBoardCode") or board
        except (HttpRequestError, json.JSONDecodeError):
            # The configured board remains valid when this optional discovery call is unavailable.
            pass

        board_url = f"https://{host}/{culture}/{client}/{board}"
        csrf_text, csrf_headers = request_text_with_headers(
            "GET",
            f"https://{host}/api/auth/csrf",
            headers={"Accept": "application/json", "Referer": board_url},
            timeout=self.timeout,
        )
        try:
            csrf_data = json.loads(csrf_text)
        except json.JSONDecodeError as exc:
            raise HttpRequestError(f"Dayforce did not return a CSRF token for {client}") from exc
        csrf_token = first_clean(csrf_data, "csrfToken", "token") if isinstance(csrf_data, dict) else ""
        if not csrf_token:
            raise HttpRequestError(f"Dayforce did not return a CSRF token for {client}")
        request_headers = {
            "Accept": "application/json",
            "Origin": f"https://{host}",
            "Referer": board_url,
            "X-CSRF-TOKEN": csrf_token,
        }
        cookie_header = response_cookie_header(csrf_headers)
        if cookie_header:
            request_headers["Cookie"] = cookie_header

        for page in range(max_pages):
            data = request_json(
                "POST",
                endpoint,
                payload={
                    "clientNamespace": client,
                    "jobBoardCode": board,
                    "cultureCode": culture,
                    "distanceUnit": 0,
                    "paginationStart": page * page_size,
                },
                headers=request_headers,
                timeout=self.timeout,
            )
            candidates = (
                data.get("jobPostings")
                or data.get("jobPostingSummaries")
                or (data.get("searchResult") or {}).get("jobPostings")
                or (data.get("result") or {}).get("jobPostings")
                or []
            )
            if not isinstance(candidates, list) or not candidates:
                break
            for item in candidates:
                if not isinstance(item, dict):
                    continue
                external_id = first_clean(
                    item,
                    "jobPostingId",
                    "postingId",
                    "id",
                    "externalJobPostingId",
                    "jobId",
                    "requisitionId",
                )
                direct_url = first_clean(item, "jobPostingUrl", "jobUrl", "applyUrl", "url")
                url = urljoin(board_url + "/", direct_url) if direct_url else (
                    f"{board_url}/jobs/{external_id}" if external_id else ""
                )
                location = first_clean(
                    item,
                    "location",
                    "locationName",
                    "locationString",
                    "postingLocation",
                    "displayLocation",
                    "jobLocation",
                    "cityState",
                ) or structured_location(item, ("postingLocations", "locations", "locationAddress", "address"))
                job = Job(
                    company=source["company"],
                    title=first_clean(item, "jobTitle", "title", "postingTitle", "requisitionTitle"),
                    location=location,
                    url=url,
                    source="Dayforce",
                    external_id=external_id,
                    posted=first_clean(
                        item,
                        "postingStartTimestampUTC",
                        "postingStartDateUTC",
                        "postingDate",
                        "postedDate",
                        "publishDate",
                    ),
                    description=first_clean(
                        item,
                        "jobDescription",
                        "description",
                        "fullDescription",
                        "shortDescription",
                        "overview",
                    ),
                )
                if job.title and job.url:
                    jobs[job.stable_id] = job
            total = int(data.get("maxCount", data.get("totalCount", 0)) or 0)
            if len(candidates) < page_size or (total and (page + 1) * page_size >= total):
                break
        return list(jobs.values())

    def _icims(self, source: dict[str, Any]) -> list[Job]:
        host = source["host"].strip().rstrip("/")
        current_url = f"https://{host}/jobs/search?ss=1&in_iframe=1"
        max_pages = int(source.get("max_pages", 10))
        jobs: dict[str, Job] = {}
        seen_pages: set[str] = set()

        for _ in range(max_pages):
            if current_url in seen_pages:
                break
            seen_pages.add(current_url)
            page_html = request_text("GET", current_url, timeout=self.timeout)
            card_pattern = re.compile(
                r"<li[^>]*class=[\"'][^\"']*iCIMS_JobCardItem[^\"']*[\"'][^>]*>"
                r"([\s\S]*?)</li>",
                re.IGNORECASE,
            )
            cards = card_pattern.findall(page_html)
            segments = cards or [page_html]
            for card in segments:
                links = re.findall(
                    r"<a[^>]*href=[\"']([^\"']*/jobs/\d+[^\"']*)[\"'][^>]*>"
                    r"([\s\S]*?)</a>",
                    card,
                    re.IGNORECASE,
                )
                for href, link_body in links:
                    if "/jobs/intro" in href.casefold():
                        continue
                    url = urljoin(f"https://{host}/", html.unescape(href))
                    title_match = re.search(r"<h[1-6][^>]*>([\s\S]*?)</h[1-6]>", link_body, re.I)
                    title = clean_text(title_match.group(1) if title_match else link_body)
                    location_match = re.search(
                        r"field-label[\"']?>\s*Location\s*</span>[\s\S]*?"
                        r"iCIMS_JobHeaderData[^>]*>\s*<span[^>]*>([\s\S]*?)</span>",
                        card,
                        re.IGNORECASE,
                    )
                    if not location_match:
                        location_match = re.search(
                            r"glyphicons-map-marker[\s\S]*?<dd[^>]*>\s*<span[^>]*>([\s\S]*?)</span>",
                            card,
                            re.IGNORECASE,
                        )
                    posted_match = re.search(
                        r"field-label[\"']?>\s*Date Posted\s*</span>[\s\S]*?"
                        r"(?:title=[\"']([^\"']+)[\"'])?[^>]*>\s*([^<]*)",
                        card,
                        re.IGNORECASE,
                    )
                    id_match = re.search(r"/jobs/(\d+)", url)
                    job = Job(
                        company=source["company"],
                        title=title,
                        location=clean_text(location_match.group(1) if location_match else ""),
                        url=url,
                        source="iCIMS",
                        external_id=id_match.group(1) if id_match else canonical_url(url),
                        posted=clean_text(
                            (posted_match.group(1) or posted_match.group(2)) if posted_match else ""
                        ),
                        description=clean_text(card),
                    )
                    if job.title and job.url:
                        jobs[job.stable_id] = job

            next_match = re.search(
                r"<link[^>]*(?:rel=[\"']next[\"'][^>]*href|href)=[\"']([^\"']+)[\"']"
                r"[^>]*(?:rel=[\"']next[\"'])?",
                page_html,
                re.IGNORECASE,
            )
            if not next_match:
                break
            next_url = urljoin(current_url, html.unescape(next_match.group(1)))
            separator = "&" if "?" in next_url else "?"
            current_url = next_url if "in_iframe=" in next_url else f"{next_url}{separator}in_iframe=1"
        return list(jobs.values())

    def _adp_workforcenow(self, source: dict[str, Any]) -> list[Job]:
        cid = source["cid"]
        cc_id = source["cc_id"]
        api_base = "https://workforcenow.adp.com/mascsr/default/careercenter/public/events/staffing/v1"
        board_url = (
            "https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html?"
            f"{urlencode({'cid': cid, 'ccId': cc_id, 'lang': 'en_US'})}"
        )
        data = request_json(
            "GET",
            f"{api_base}/job-requisitions",
            params={"cid": cid, "ccId": cc_id},
            timeout=self.timeout,
        )
        jobs: list[Job] = []
        for item in data.get("jobRequisitions", []):
            if not isinstance(item, dict):
                continue
            external_id = first_clean(item, "itemID")
            direct_url = ""
            for link in item.get("links", []) if isinstance(item.get("links"), list) else []:
                if isinstance(link, dict) and link.get("href"):
                    direct_url = urljoin(board_url, str(link["href"]))
                    break
            jobs.append(
                Job(
                    company=source["company"],
                    title=first_clean(item, "requisitionTitle"),
                    location=structured_location(item, ("requisitionLocations",)),
                    url=direct_url or f"{board_url}&jobId={external_id}",
                    source="ADP Workforce Now",
                    external_id=external_id,
                    posted=first_clean(item, "postDate"),
                    description=first_clean(item, "requisitionDescription"),
                )
            )
        return jobs

    def _adp_myjobs(self, source: dict[str, Any]) -> list[Job]:
        slug = source["slug"]
        board_url = f"https://myjobs.adp.com/{slug}/cx/job-listing"
        career_site = request_json(
            "GET",
            f"https://myjobs.adp.com/public/staffing/v1/career-site/{slug}",
            timeout=self.timeout,
        )
        token = first_clean(career_site, "myJobsToken")
        properties = career_site.get("properties") if isinstance(career_site.get("properties"), dict) else {}
        api_origin = first_clean(properties, "myadpUrl").rstrip("/")
        if not token or not api_origin:
            raise HttpRequestError(f"ADP MyJobs did not expose its public token for {slug}")

        jobs: dict[str, Job] = {}
        page_size = 100
        max_pages = int(source.get("max_pages", 10))
        for page in range(max_pages):
            data = request_json(
                "GET",
                f"{api_origin}/myadp_prefix/mycareer/public/staffing/v1/"
                "job-requisitions/apply-custom-filters",
                params={
                    "$select": (
                        "reqId,jobTitle,publishedJobTitle,type,jobDescription,jobQualifications,"
                        "workLocations,clientRequisitionID,postingDate,requisitionLocations,"
                        "postingLocations,organizationalUnits"
                    ),
                    "$top": str(page_size),
                    "$skip": str(page * page_size),
                    "$filter": "",
                    "radius": "25",
                    "tz": "America/New_York",
                },
                headers={
                    "myjobstoken": token,
                    "rolecode": "manager",
                    "Origin": "https://myjobs.adp.com",
                    "Referer": board_url,
                },
                timeout=self.timeout,
            )
            postings = data.get("jobRequisitions", [])
            if not isinstance(postings, list) or not postings:
                break
            for item in postings:
                if not isinstance(item, dict):
                    continue
                external_id = first_clean(item, "reqId", "clientRequisitionID")
                job = Job(
                    company=source["company"],
                    title=first_clean(item, "publishedJobTitle", "jobTitle"),
                    location=structured_location(
                        item,
                        ("requisitionLocations", "workLocations", "postingLocations"),
                    ),
                    url=(
                        first_clean(item, "url", "jobUrl")
                        or f"https://myjobs.adp.com/{slug}/cx/job-details?reqId={external_id}"
                    ),
                    source="ADP MyJobs",
                    external_id=external_id,
                    posted=first_clean(item, "postingDate"),
                    description=" ".join(
                        filter(
                            None,
                            [
                                first_clean(item, "jobDescription"),
                                first_clean(item, "jobQualifications"),
                                " ".join(
                                    first_clean(unit, "name")
                                    or first_clean(
                                        unit.get("nameCode", {}) if isinstance(unit, dict) else {},
                                        "longName",
                                    )
                                    for unit in item.get("organizationalUnits", [])
                                    if isinstance(unit, dict)
                                ),
                            ],
                        )
                    ),
                )
                if job.title and job.url:
                    jobs[job.stable_id] = job
            total = int(data.get("count", 0) or 0)
            if len(postings) < page_size or (total and (page + 1) * page_size >= total):
                break
        return list(jobs.values())

    def _csod(self, source: dict[str, Any]) -> list[Job]:
        host = source["host"].strip().rstrip("/")
        site_id = int(source["site_id"])
        corp = source.get("corp", host.split(".", 1)[0])
        home_url = f"https://{host}/ux/ats/careersite/{site_id}/home?c={corp}"
        bootstrap, response_headers = request_text_with_headers(
            "GET",
            home_url,
            timeout=self.timeout,
        )
        token_match = re.search(r'"token"\s*:\s*"([A-Za-z0-9._-]+)"', bootstrap)
        if not token_match:
            raise HttpRequestError(f"Cornerstone did not expose an anonymous token for {host}")
        token = token_match.group(1)

        cookie_header = response_cookie_header(response_headers)
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Referer": home_url,
        }
        if cookie_header:
            headers["Cookie"] = cookie_header

        jobs: dict[str, Job] = {}
        page_size = 25
        max_pages = int(source.get("max_pages_per_term", 20))
        terms = source.get("search_terms", ["intern", "co-op"])
        endpoint = f"https://{host}/services/x/career-site/v1/search"
        for term in terms:
            for page in range(1, max_pages + 1):
                data = request_json(
                    "POST",
                    endpoint,
                    payload={
                        "careerSiteId": site_id,
                        "careerSitePageId": site_id,
                        "pageNumber": page,
                        "pageSize": page_size,
                        "cultureId": 1,
                        "cultureName": "en-US",
                        "searchText": term,
                        "states": [],
                        "countryCodes": [],
                        "cities": [],
                        "placeID": "",
                        "radius": None,
                        "postingsWithinDays": None,
                        "customFieldCheckboxKeys": [],
                        "customFieldDropdowns": [],
                        "customFieldRadios": [],
                    },
                    headers=headers,
                    timeout=self.timeout,
                )
                wrapper = data.get("data", {}) if isinstance(data, dict) else {}
                requisitions = wrapper.get("requisitions", []) if isinstance(wrapper, dict) else []
                if not isinstance(requisitions, list) or not requisitions:
                    break
                for item in requisitions:
                    if not isinstance(item, dict):
                        continue
                    external_id = first_clean(item, "requisitionId")
                    locations: list[str] = []
                    for location in item.get("locations", []) if isinstance(item.get("locations"), list) else []:
                        if not isinstance(location, dict):
                            continue
                        label = ", ".join(
                            filter(
                                None,
                                [
                                    first_clean(location, "city"),
                                    first_clean(location, "state"),
                                    first_clean(location, "country"),
                                ],
                            )
                        )
                        if label and label not in locations:
                            locations.append(label)
                    job = Job(
                        company=source["company"],
                        title=clean_text(item.get("displayJobTitle")),
                        location=" / ".join(locations),
                        url=(
                            f"https://{host}/ux/ats/careersite/{site_id}/home/requisition/"
                            f"{external_id}?c={corp}"
                        ),
                        source="Cornerstone",
                        external_id=external_id,
                        posted=first_clean(item, "postingEffectiveDate"),
                    )
                    if job.title and external_id:
                        jobs[job.stable_id] = job
                total = int(wrapper.get("totalCount", 0) or 0)
                if len(requisitions) < page_size or (total and page * page_size >= total):
                    break
        return list(jobs.values())

    def _static(self, source: dict[str, Any]) -> list[Job]:
        page_url = source["page_url"]
        jobs: dict[str, Job] = {}
        href_pattern = re.compile(source.get("anchor_href_pattern", r".+"), re.IGNORECASE)
        title_location_pattern = (
            re.compile(source["title_location_regex"], re.IGNORECASE)
            if source.get("title_location_regex")
            else None
        )
        default_location = source.get("default_location", "")

        def add_job(title_value: str, location_value: str, url: str) -> None:
            title = clean_text(title_value)
            location = clean_text(location_value)
            if title_location_pattern:
                match = title_location_pattern.search(title)
                if not match:
                    location = ""
                else:
                    title = clean_text(match.groupdict().get("title", title))
                    location = clean_text(match.groupdict().get("location", location))
            if not title:
                return
            job = Job(
                company=source["company"],
                title=title,
                location=location,
                url=url,
                source="Official careers page",
                external_id=f"{title}|{canonical_url(url)}",
            )
            jobs[job.stable_id] = job

        def parse_html(page_html: str) -> None:
            for href, body in re.findall(
                r"<a[^>]*href=[\"']([^\"']+)[\"'][^>]*>([\s\S]*?)</a>",
                page_html,
                re.IGNORECASE,
            ):
                absolute_url = urljoin(page_url, html.unescape(href))
                if href_pattern.search(absolute_url):
                    add_job(body, default_location, absolute_url)

            if source.get("include_headings", False):
                for body in re.findall(
                    r"<h[1-6][^>]*>([\s\S]*?)</h[1-6]>", page_html, re.IGNORECASE
                ):
                    add_job(body, default_location, page_url)

        def parse_markdown(markdown: str) -> None:
            lines = markdown.splitlines()
            link_pattern = re.compile(r"(?<!!)\[([^\]]+)\]\(([^)]+)\)")
            for index, line in enumerate(lines):
                for match in link_pattern.finditer(line):
                    href = html.unescape(match.group(2).strip().split()[0])
                    absolute_url = urljoin(page_url, href)
                    if not href_pattern.search(absolute_url):
                        continue
                    title = clean_text(re.sub(r"[*_`]+", "", match.group(1)))
                    location = default_location
                    if title_location_pattern:
                        same_line = clean_text(
                            re.sub(link_pattern, lambda value: value.group(1), line)
                        )
                        candidates = [same_line]
                        for following in lines[index + 1 : index + 4]:
                            following = clean_text(re.sub(r"[*_`]+", "", following))
                            if following:
                                candidates.append(f"{title} {following}")
                                break
                        title = next(
                            (
                                candidate
                                for candidate in candidates
                                if title_location_pattern.search(candidate)
                            ),
                            title,
                        )
                        location = ""
                    add_job(title, location, absolute_url)

            if source.get("include_headings", False):
                for line in lines:
                    match = re.match(r"^\s*#{1,6}\s+(.+?)\s*$", line)
                    if match:
                        add_job(re.sub(r"[*_`]+", "", match.group(1)), default_location, page_url)

        direct_error: HttpRequestError | None = None
        try:
            page_html = request_text("GET", page_url, timeout=self.timeout)
            if re.search(r"cf-chl|cloudflare|just a moment", page_html, re.IGNORECASE):
                raise HttpRequestError(f"Anti-bot challenge returned for {page_url}")
            parse_html(page_html)
        except HttpRequestError as exc:
            direct_error = exc

        fallback_url = source.get("reader_fallback_url", "").strip()
        if fallback_url and (direct_error is not None or not jobs):
            markdown = request_text("GET", fallback_url, timeout=self.timeout)
            parse_markdown(markdown)
        elif direct_error is not None:
            raise direct_error

        return list(jobs.values())


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def prune_seen(seen: dict[str, str], retention_days: int) -> dict[str, str]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    kept: dict[str, str] = {}
    for key, value in seen.items():
        try:
            if datetime.fromisoformat(value.replace("Z", "+00:00")) >= cutoff:
                kept[key] = value
        except (ValueError, AttributeError):
            continue
    return kept


def discord_embeds(jobs: list[RankedJob]) -> list[dict[str, Any]]:
    embeds: list[dict[str, Any]] = []
    for ranked in jobs:
        job = ranked.job
        fields = [
            {"name": "Company", "value": job.company, "inline": True},
            {"name": "Location", "value": job.location or "Not listed", "inline": True},
            {"name": "Category", "value": " · ".join(ranked.tags), "inline": False},
        ]
        if job.posted:
            fields.append({"name": "Posted", "value": job.posted[:100], "inline": True})
        fields.append({"name": "AI match score", "value": f"{min(ranked.score, 100)}/100", "inline": True})
        if ranked.ai_reason:
            fields.append({"name": "Why it matches", "value": ranked.ai_reason[:1024], "inline": False})
        if ranked.ai_evidence:
            fields.append({"name": "Evidence checked", "value": ranked.ai_evidence[:1024], "inline": False})
        embeds.append(
            {
                "title": job.title[:256],
                "url": job.url,
                "color": 0x4F46E5,
                "fields": fields,
                "footer": {"text": f"Source: {job.source} · AI-screened; verify details before applying"},
            }
        )
    return embeds


def validate_webhook(url: str) -> None:
    parsed = urlparse(url)
    allowed_hosts = {"discord.com", "discordapp.com", "canary.discord.com", "ptb.discord.com"}
    if parsed.scheme != "https" or parsed.hostname not in allowed_hosts or "/api/webhooks/" not in parsed.path:
        raise ValueError("DISCORD_WEBHOOK_URL is not a valid Discord incoming webhook URL")


def post_discord(webhook: str, ranked_jobs: list[RankedJob]) -> None:
    validate_webhook(webhook)
    embeds = discord_embeds(ranked_jobs)
    total_batches = max(1, (len(embeds) + 9) // 10)
    for index in range(0, len(embeds), 10):
        batch_number = index // 10 + 1
        payload = {
            "username": DISCORD_BOT_NAME,
            "content": (
                "Help me build more AI data centers! Take a peek at these roles.\n"
                f"**New semiconductor opportunities — {datetime.now().strftime('%B %d, %Y')}** "
                f"({batch_number}/{total_batches})"
            ),
            "embeds": embeds[index : index + 10],
            "allowed_mentions": {"parse": []},
        }
        request_json("POST", webhook, payload=payload, params={"wait": "true"}, timeout=25)
        time.sleep(0.8)


def post_scan_summary(
    webhook: str,
    *,
    successful_sources: int,
    configured_sources: int,
    matching_jobs: int,
) -> None:
    """Confirm that a scan ran when there were no new alerts to display."""
    validate_webhook(webhook)
    icon = "✅" if successful_sources == configured_sources else "⚠️"
    payload = {
        "username": DISCORD_BOT_NAME,
        "content": (
            f"{icon} **Daily semiconductor scan complete — "
            f"{datetime.now().strftime('%B %d, %Y')}**\n"
            f"Checked **{successful_sources}/{configured_sources}** company feeds and found "
            f"**{matching_jobs}** active targeted U.S. opportunities.\n"
            "**No new opportunities today.** I’ll keep looking tomorrow."
        ),
        "allowed_mentions": {"parse": []},
    }
    request_json("POST", webhook, payload=payload, params={"wait": "true"}, timeout=25)


def post_test(webhook: str) -> None:
    validate_webhook(webhook)
    payload = {
        "username": DISCORD_BOT_NAME,
        "content": (
            "✅ Jensen Huang is connected. Daily semiconductor scans are ready for testing.\n\n"
            "Help me build more AI data centers! Take a peek at these roles."
        ),
        "allowed_mentions": {"parse": []},
    }
    request_json("POST", webhook, payload=payload, params={"wait": "true"}, timeout=25)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--dry-run", action="store_true", help="Print matches without posting or changing state")
    parser.add_argument("--send-test", action="store_true", help="Send one Discord connectivity message")
    parser.add_argument(
        "--post-summary",
        action="store_true",
        help="Post a completion message when a live scan has no new opportunities",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    webhook = os.getenv("DISCORD_WEBHOOK_URL", "").strip()

    if args.send_test:
        if not webhook:
            logging.error("DISCORD_WEBHOOK_URL is missing")
            return 2
        post_test(webhook)
        logging.info("Discord test message sent")
        return 0

    config = load_json(args.config, {})
    settings = config.get("settings", {})
    ai_settings = settings.get("ai", {})
    ai_enabled = bool(ai_settings.get("enabled", True))
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    if ai_enabled and not gemini_key:
        logging.error(
            "GEMINI_API_KEY is missing; refusing to silently fall back to the old keyword-only scan"
        )
        return 2
    seen = prune_seen(
        load_json(args.state, {}),
        int(settings.get("seen_retention_days", 365)),
    )
    scanner = Scanner(timeout=int(settings.get("request_timeout_seconds", 25)))
    all_jobs: dict[str, Job] = {}
    successful_sources = 0
    configured_sources = sum(
        1 for source in config.get("sources", []) if source.get("enabled", True)
    )

    for source in config.get("sources", []):
        if not source.get("enabled", True):
            continue
        try:
            jobs = scanner.scan(source)
            successful_sources += 1
            logging.info("%s: fetched %d jobs", source["company"], len(jobs))
            for job in jobs:
                if job.url and job.title:
                    all_jobs[job.stable_id] = job
        except (HttpRequestError, ValueError, KeyError, json.JSONDecodeError) as exc:
            logging.warning("%s source failed: %s", source.get("company", "Unknown"), exc)

    if successful_sources == 0:
        logging.error("Every configured job source failed; state was not changed")
        return 1

    if ai_enabled and ai_settings.get("discovery_enabled", True):
        try:
            discovered = discover_jobs_with_gemini(
                config.get("sources", []),
                api_key=gemini_key,
                model=os.getenv(
                    "GEMINI_DISCOVERY_MODEL",
                    clean_text(ai_settings.get("discovery_model")) or DEFAULT_GEMINI_DISCOVERY_MODEL,
                ),
                companies_per_query=int(ai_settings.get("companies_per_query", 6)),
            )
            logging.info("Gemini discovery agent found %d additional candidates", len(discovered))
            for job in discovered:
                all_jobs[job.stable_id] = job
        except (HttpRequestError, ValueError, json.JSONDecodeError) as exc:
            logging.warning("Gemini discovery agent failed; continuing with ATS candidates: %s", exc)

    unique_jobs = deduplicate_jobs(all_jobs.values())
    if ai_enabled:
        ranked = review_jobs_with_gemini(
            unique_jobs,
            api_key=gemini_key,
            model=os.getenv(
                "GEMINI_MODEL",
                clean_text(ai_settings.get("model")) or DEFAULT_GEMINI_MODEL,
            ),
            minimum_confidence=int(ai_settings.get("minimum_confidence", 72)),
            batch_size=int(ai_settings.get("review_batch_size", 12)),
        )
    else:
        ranked = rank_jobs(unique_jobs, us_only=bool(settings.get("us_only", True)))
    new_ranked = [item for item in ranked if item.job.stable_id not in seen]
    limit = int(settings.get("max_alerts_per_run", 20))
    alerts = new_ranked[:limit]

    logging.info(
        "Found %d matching jobs, %d new, sending %d",
        len(ranked),
        len(new_ranked),
        len(alerts),
    )

    if args.dry_run:
        print(json.dumps([{**asdict(item.job), "score": item.score, "tags": item.tags} for item in alerts], indent=2))
        return 0

    if alerts:
        if not webhook:
            logging.error("DISCORD_WEBHOOK_URL is missing")
            return 2
        post_discord(webhook, alerts)
    elif args.post_summary:
        if not webhook:
            logging.error("DISCORD_WEBHOOK_URL is missing")
            return 2
        post_scan_summary(
            webhook,
            successful_sources=successful_sources,
            configured_sources=configured_sources,
            matching_jobs=len(ranked),
        )

    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    # Mark every current match as seen. This prevents a first run with many jobs
    # from leaking the remainder into repeated daily alerts.
    for item in ranked:
        seen[item.job.stable_id] = now
    save_json(args.state, seen)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        logging.exception("Unexpected scanner failure")
        sys.exit(1)
