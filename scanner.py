#!/usr/bin/env python3
"""Free semiconductor opportunity scanner for SCRO at UCF.

The scanner reads public applicant-tracking-system feeds, scores early-career
roles with a manufacturing preference, suppresses duplicates, and sends new
matches to a Discord incoming webhook.
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
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = ROOT / "companies.json"
DEFAULT_STATE = ROOT / "seen_jobs.json"
USER_AGENT = "SCRO-Opportunity-Bot/1.0 (+https://github.com/Mahkri-glitch/scro-opportunity-bot)"
DISCORD_BOT_NAME = "Jensen Huang"

EARLY_CAREER_PATTERNS = (
    r"\bintern(ship)?\b",
    r"\bco[ -]?op\b",
    r"\bstudent\b",
    r"\buniversity\b",
    r"\bapprentice(ship)?\b",
    r"\bearly career\b",
    r"\brecent (college )?graduate\b",
    r"\bnew (college )?grad(uate)?\b",
    r"\bgraduate (engineer|program|programme|rotation)\b",
    r"\brotational program\b",
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


class HttpRequestError(RuntimeError):
    """Raised when a remote job feed or Discord request fails."""


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


def canonical_url(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{parsed.path.rstrip('/')}"


def clean_text(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def matches_any(text: str, patterns: Iterable[str]) -> bool:
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


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

    if not matches_any(title, EARLY_CAREER_PATTERNS):
        return None
    if matches_any(title, SENIOR_PATTERNS) and "intern" not in title_lower:
        return None
    if matches_any(title, DOCTORAL_TITLE_PATTERNS) and not (
        matches_any(title, BACHELOR_PATTERNS) or matches_any(title, MASTER_PATTERNS)
    ):
        return None
    if us_only and not is_us_based(title, job.location):
        return None

    score = 5
    tags: list[str] = [opportunity_type(title)]

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

    if any(term in title_lower for term in EXCLUDED_FUNCTION_TERMS):
        score -= 8
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


def opportunity_type(title: str) -> str:
    lowered = title.casefold()
    if "co-op" in lowered or "coop" in lowered or "co op" in lowered:
        return "Co-op"
    if "apprent" in lowered:
        return "Apprenticeship"
    if "graduate" in lowered or "early career" in lowered or "rotation" in lowered:
        return "New Grad"
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
        raise ValueError(f"Unsupported source type: {source_type!r}")

    def _workday(self, source: dict[str, Any]) -> list[Job]:
        host = source["host"].strip().rstrip("/")
        tenant = source["tenant"]
        site = source["site"]
        endpoint = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
        terms = source.get("search_terms", ["intern", "co-op", "early career"])
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
                if matches_any(job.title, EARLY_CAREER_PATTERNS)
                and not has_non_us_marker(f"{job.title} {job.location}")
            ]
            candidates.sort(
                key=lambda job: (score_job(job, us_only=False) or RankedJob(job, 0, ())).score,
                reverse=True,
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
        fields.append({"name": "Priority score", "value": str(ranked.score), "inline": True})
        embeds.append(
            {
                "title": job.title[:256],
                "url": job.url,
                "color": 0x4F46E5,
                "fields": fields,
                "footer": {"text": f"Source: {job.source} · Verify requirements on the official posting"},
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
    seen = prune_seen(
        load_json(args.state, {}),
        int(settings.get("seen_retention_days", 365)),
    )
    scanner = Scanner(timeout=int(settings.get("request_timeout_seconds", 25)))
    all_jobs: dict[str, Job] = {}
    successful_sources = 0

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

    ranked = rank_jobs(all_jobs.values(), us_only=bool(settings.get("us_only", True)))
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
