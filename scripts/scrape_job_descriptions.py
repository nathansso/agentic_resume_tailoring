"""
Scrape real job descriptions into the tailoring-benchmark dataset (issue #51).

Pulls postings from public, no-auth job-board APIs (Greenhouse and Lever),
filters them to software/ML roles, strips HTML, and writes one JSON file per
posting under eval/jd_dataset/. The dataset is checked in so the benchmark is
reproducible offline; re-run this script only when you want to refresh it.

Usage:
    python scripts/scrape_job_descriptions.py                 # default boards
    python scripts/scrape_job_descriptions.py --per-board 3
    python scripts/scrape_job_descriptions.py --greenhouse datadog stripe --lever plaid

Each output file:
    {
      "id": "<company>_<slug>",
      "source": "greenhouse|lever",
      "company": "...", "title": "...", "location": "...", "url": "...",
      "description": "<plain text>", "scraped_at": "<ISO date>"
    }
"""
import argparse
import html
import json
import re
import sys
from datetime import date
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
DATASET_DIR = ROOT / "eval" / "jd_dataset"

# Public board tokens — every one of these is a documented, unauthenticated API.
DEFAULT_GREENHOUSE = ["datadog", "stripe", "duolingo", "samsara", "robinhood"]
DEFAULT_LEVER = ["plaid", "voleon"]

# Role filter: the benchmark targets the kinds of jobs ART's users tailor for.
INCLUDE = re.compile(
    r"software engineer|machine learning|ml engineer|data scientist|ai engineer"
    r"|backend|back.end|full.stack|data engineer|research engineer",
    re.I,
)
EXCLUDE = re.compile(
    r"director|manager|principal|staff|vp|head of|intern|contract|distinguished",
    re.I,
)

MIN_DESCRIPTION_CHARS = 800  # skip stub postings with no real requirements text
TIMEOUT = 30


def _clean_html(raw: str) -> str:
    """Job-board APIs return HTML (Greenhouse double-escapes it). → plain text."""
    text = html.unescape(raw or "")
    soup = BeautifulSoup(text, "html.parser")
    for li in soup.find_all("li"):
        li.insert_before("\n- ")
    for block in soup.find_all(["p", "div", "br", "h1", "h2", "h3", "h4", "ul"]):
        block.insert_before("\n")
    text = soup.get_text()
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _slug(text: str, max_len: int = 48) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:max_len]


def _role_ok(title: str) -> bool:
    return bool(INCLUDE.search(title)) and not EXCLUDE.search(title)


def fetch_greenhouse(board: str, per_board: int) -> list[dict]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"
    resp = requests.get(url, timeout=TIMEOUT)
    resp.raise_for_status()
    postings = []
    seen: set[str] = set()
    for job in resp.json().get("jobs", []):
        title = job.get("title", "")
        if not _role_ok(title):
            continue
        description = _clean_html(job.get("content", ""))
        if len(description) < MIN_DESCRIPTION_CHARS:
            continue
        company = (job.get("company_name") or board).strip()
        posting_id = f"{_slug(company)}_{_slug(title)}"
        if posting_id in seen:  # same role posted in several locations
            continue
        seen.add(posting_id)
        postings.append({
            "id": posting_id,
            "source": "greenhouse",
            "company": company,
            "title": title,
            "location": (job.get("location") or {}).get("name", ""),
            "url": job.get("absolute_url", ""),
            "description": description,
            "scraped_at": date.today().isoformat(),
        })
        if len(postings) >= per_board:
            break
    return postings


def fetch_lever(company: str, per_board: int) -> list[dict]:
    url = f"https://api.lever.co/v0/postings/{company}?mode=json"
    resp = requests.get(url, timeout=TIMEOUT)
    resp.raise_for_status()
    postings = []
    seen: set[str] = set()
    for job in resp.json():
        title = job.get("text", "")
        if not _role_ok(title):
            continue
        posting_id = f"{_slug(company)}_{_slug(title)}"
        if posting_id in seen:
            continue
        parts = [job.get("descriptionPlain") or _clean_html(job.get("description", ""))]
        for lst in job.get("lists", []):
            parts.append(lst.get("text", ""))
            parts.append(_clean_html(lst.get("content", "")))
        description = "\n\n".join(p for p in parts if p).strip()
        if len(description) < MIN_DESCRIPTION_CHARS:
            continue
        seen.add(posting_id)
        postings.append({
            "id": posting_id,
            "source": "lever",
            "company": company,
            "title": title,
            "location": (job.get("categories") or {}).get("location", ""),
            "url": job.get("hostedUrl", ""),
            "description": description,
            "scraped_at": date.today().isoformat(),
        })
        if len(postings) >= per_board:
            break
    return postings


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--greenhouse", nargs="*", default=DEFAULT_GREENHOUSE, help="Greenhouse board tokens")
    ap.add_argument("--lever", nargs="*", default=DEFAULT_LEVER, help="Lever company tokens")
    ap.add_argument("--per-board", type=int, default=2, help="max postings kept per board")
    ap.add_argument("--out", type=Path, default=DATASET_DIR, help="output directory")
    args = ap.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    total = 0
    for board in args.greenhouse:
        try:
            postings = fetch_greenhouse(board, args.per_board)
        except Exception as e:  # one bad board must not kill the refresh
            print(f"greenhouse/{board}: FAILED ({e})", file=sys.stderr)
            continue
        for p in postings:
            (args.out / f"{p['id']}.json").write_text(
                json.dumps(p, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            print(f"greenhouse/{board}: {p['title']} ({len(p['description'])} chars)")
            total += 1
    for company in args.lever:
        try:
            postings = fetch_lever(company, args.per_board)
        except Exception as e:
            print(f"lever/{company}: FAILED ({e})", file=sys.stderr)
            continue
        for p in postings:
            (args.out / f"{p['id']}.json").write_text(
                json.dumps(p, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            print(f"lever/{company}: {p['title']} ({len(p['description'])} chars)")
            total += 1

    print(f"\n{total} postings written to {args.out}")
    return 0 if total else 1


if __name__ == "__main__":
    raise SystemExit(main())
