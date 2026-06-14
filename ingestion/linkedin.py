import logging
import time
from typing import Dict, Any, List, Optional

import requests

from config import BRIGHTDATA_API_KEY, BRIGHTDATA_LINKEDIN_DATASET_ID

logger = logging.getLogger(__name__)

# Bright Data Web Scraper API (issue 13)
_BRIGHTDATA_BASE = "https://api.brightdata.com/datasets/v3"
# Total time we'll wait for a snapshot to become ready before giving up.
_POLL_TIMEOUT_SECONDS = 180
_POLL_INTERVAL_SECONDS = 5


class LinkedInIngestionError(RuntimeError):
    """User-facing error raised when LinkedIn ingestion fails."""


class LinkedInIngestor:
    """
    LinkedIn profile ingestor with two modes:
    1. Bright Data Web Scraper API (preferred) — structured profile data
    2. PDF fallback — parses a LinkedIn PDF export via Docling
    """

    @staticmethod
    def _normalize_url(profile_url: str) -> str:
        """Accept either a full LinkedIn URL or a bare username/handle."""
        profile_url = (profile_url or "").strip().rstrip("/")
        if not profile_url:
            raise LinkedInIngestionError("No LinkedIn profile URL or username provided.")
        if not profile_url.startswith("http"):
            # Treat as a username/handle
            handle = profile_url.lstrip("@").split("/")[-1]
            profile_url = f"https://www.linkedin.com/in/{handle}"
        return profile_url

    def ingest_brightdata(self, profile_url: str) -> Dict[str, Any]:
        """
        Scrape a LinkedIn profile via Bright Data's Web Scraper API.

        Flow: trigger a collection -> poll progress until ready -> download the
        snapshot -> flatten the structured record into profile text.

        Args:
            profile_url: Full LinkedIn profile URL or a bare username.

        Returns:
            Dict with source_type, source_file, full_text (structured profile text).
        """
        if not BRIGHTDATA_API_KEY:
            raise LinkedInIngestionError(
                "LinkedIn import is not configured on this server. "
                "Upload a LinkedIn PDF export instead."
            )

        profile_url = self._normalize_url(profile_url)
        logger.info("Bright Data: triggering LinkedIn scrape for %s", profile_url)

        headers = {
            "Authorization": f"Bearer {BRIGHTDATA_API_KEY}",
            "Content-Type": "application/json",
        }

        snapshot_id = self._trigger(profile_url, headers)
        self._wait_until_ready(snapshot_id, headers)
        record = self._download(snapshot_id, headers)

        profile_text = self._brightdata_to_text(record, profile_url)
        if len(profile_text.strip()) < 50:
            raise LinkedInIngestionError(
                "LinkedIn scrape returned no usable profile data. "
                "Double-check the profile URL, or upload a PDF export instead."
            )

        logger.info(
            "Bright Data: scraped %s (%d chars)",
            record.get("name", "profile"),
            len(profile_text),
        )
        return {
            "source_type": "linkedin",
            "source_file": f"linkedin:{profile_url}",
            "full_text": profile_text,
        }

    def _trigger(self, profile_url: str, headers: Dict[str, str]) -> str:
        """Kick off a collection and return the snapshot id."""
        try:
            resp = requests.post(
                f"{_BRIGHTDATA_BASE}/trigger",
                headers=headers,
                params={
                    "dataset_id": BRIGHTDATA_LINKEDIN_DATASET_ID,
                    "include_errors": "true",
                },
                json=[{"url": profile_url}],
                timeout=30,
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise LinkedInIngestionError(
                f"Could not reach the LinkedIn import service: {exc}"
            ) from exc

        snapshot_id = (resp.json() or {}).get("snapshot_id")
        if not snapshot_id:
            raise LinkedInIngestionError(
                "LinkedIn import service did not return a snapshot id."
            )
        return snapshot_id

    def _wait_until_ready(self, snapshot_id: str, headers: Dict[str, str]) -> None:
        """Poll the progress endpoint until the snapshot is ready or fails."""
        deadline = time.monotonic() + _POLL_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            try:
                resp = requests.get(
                    f"{_BRIGHTDATA_BASE}/progress/{snapshot_id}",
                    headers=headers,
                    timeout=30,
                )
                resp.raise_for_status()
            except requests.RequestException as exc:
                raise LinkedInIngestionError(
                    f"Lost contact with the LinkedIn import service: {exc}"
                ) from exc

            status = (resp.json() or {}).get("status", "")
            if status == "ready":
                return
            if status == "failed":
                raise LinkedInIngestionError(
                    "LinkedIn import failed — the profile could not be collected."
                )
            time.sleep(_POLL_INTERVAL_SECONDS)

        raise LinkedInIngestionError(
            "LinkedIn import timed out. Please try again in a few minutes."
        )

    def _download(self, snapshot_id: str, headers: Dict[str, str]) -> Dict[str, Any]:
        """Download the snapshot and return the first profile record."""
        try:
            resp = requests.get(
                f"{_BRIGHTDATA_BASE}/snapshot/{snapshot_id}",
                headers=headers,
                params={"format": "json"},
                timeout=60,
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise LinkedInIngestionError(
                f"Could not download the LinkedIn profile data: {exc}"
            ) from exc

        data = resp.json()
        # The snapshot may be a single object or a list of records.
        if isinstance(data, list):
            records = [r for r in data if isinstance(r, dict)]
            if not records:
                raise LinkedInIngestionError(
                    "LinkedIn import returned an empty profile."
                )
            record = records[0]
        elif isinstance(data, dict):
            record = data
        else:
            raise LinkedInIngestionError(
                "LinkedIn import returned an unexpected response."
            )

        if record.get("error") or record.get("warning"):
            detail = record.get("error") or record.get("warning")
            raise LinkedInIngestionError(f"LinkedIn import error: {detail}")
        return record

    def ingest_pdf(self, file_path: str) -> Dict[str, Any]:
        """
        Fallback: Ingest a LinkedIn PDF profile export via Docling.
        """
        from docling.document_converter import DocumentConverter

        logger.info(f"Ingesting LinkedIn PDF: {file_path}")
        converter = DocumentConverter()
        result = converter.convert(file_path)

        if result.status != "success":
            raise RuntimeError(f"Docling failed: {result.errors}")

        doc = result.document
        markdown_text = doc.export_to_markdown()

        return {
            "source_type": "linkedin",
            "source_file": str(file_path),
            "full_text": markdown_text,
        }

    # Legacy compatibility
    def ingest(self, file_path: str) -> Dict[str, Any]:
        return self.ingest_pdf(file_path)

    # ------------------------------------------------------------------
    # Flattening Bright Data structured records into parser-ready text
    # ------------------------------------------------------------------
    @staticmethod
    def _scalar(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, (str, int, float)):
            return str(value).strip()
        return ""

    def _format_entry(self, entry: Any, key_order: List[str]) -> str:
        """Render a single experience/education/etc. item as one readable line."""
        if isinstance(entry, str):
            return entry.strip()
        if not isinstance(entry, dict):
            return ""
        parts: List[str] = []
        seen = set()
        # Preferred keys first, then any remaining scalar fields.
        for key in key_order + [k for k in entry.keys() if k not in key_order]:
            if key in seen:
                continue
            seen.add(key)
            val = self._scalar(entry.get(key))
            if val:
                parts.append(val)
        return " — ".join(parts)

    def _brightdata_to_text(self, record: Dict[str, Any], url: str) -> str:
        """Flatten a Bright Data LinkedIn record into text for the parser agent."""
        lines: List[str] = []

        name = self._scalar(record.get("name")) or self._scalar(record.get("full_name"))
        if name:
            lines.append(f"Name: {name}")
        lines.append(f"LinkedIn: {url}")

        headline = self._scalar(record.get("position")) or self._scalar(
            record.get("headline")
        )
        if headline:
            lines.append(f"Headline: {headline}")

        location = self._scalar(record.get("location")) or " ".join(
            p for p in [self._scalar(record.get("city")), self._scalar(record.get("country_code"))] if p
        )
        if location:
            lines.append(f"Location: {location}")

        company = record.get("current_company")
        if isinstance(company, dict):
            company = self._scalar(company.get("name"))
        company = self._scalar(company)
        if company:
            lines.append(f"Current company: {company}")

        about = self._scalar(record.get("about")) or self._scalar(record.get("summary"))
        if about:
            lines.append("")
            lines.append("About:")
            lines.append(about)

        def _section(title: str, key: str, key_order: List[str]) -> None:
            items = record.get(key)
            if not isinstance(items, list) or not items:
                return
            rendered = [self._format_entry(i, key_order) for i in items]
            rendered = [r for r in rendered if r]
            if not rendered:
                return
            lines.append("")
            lines.append(f"{title}:")
            for r in rendered:
                lines.append(f"- {r}")

        _section(
            "Experience",
            "experience",
            ["title", "company", "location", "start_date", "end_date", "duration", "description"],
        )
        _section(
            "Education",
            "education",
            ["title", "degree", "field", "start_year", "end_year", "description"],
        )
        _section("Certifications", "certifications", ["title", "name", "subtitle", "issuer"])
        _section("Languages", "languages", ["title", "name", "subtitle"])

        skills = record.get("skills")
        if isinstance(skills, list) and skills:
            rendered = [self._scalar(s) if isinstance(s, str) else self._scalar((s or {}).get("name")) for s in skills]
            rendered = [r for r in rendered if r]
            if rendered:
                lines.append("")
                lines.append("Skills: " + ", ".join(rendered))

        return "\n".join(lines)
