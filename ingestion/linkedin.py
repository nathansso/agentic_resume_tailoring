import logging
import json
import re
import shutil
from typing import Dict, Any, Optional
from pathlib import Path
from urllib.parse import urlparse
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# Path to persist browser session across runs
SESSION_DIR = Path(__file__).resolve().parent.parent / ".linkedin_session"


class LinkedInIngestor:
    """
    LinkedIn profile ingestor with two modes:
    1. Web scraping via Playwright (preferred) — scrapes live profile data
    2. PDF fallback — parses a LinkedIn PDF export via Docling
    """

    @staticmethod
    def _is_logged_in_url(url: str) -> bool:
        """Check if URL path (not query params) indicates a logged-in page."""
        path = urlparse(url).path
        return path.startswith("/in/") or path.startswith("/feed")

    def ingest_web(self, profile_url: str) -> Dict[str, Any]:
        """
        Scrape a LinkedIn profile using Playwright with a persistent browser session.
        
        On first run, opens a visible browser for manual login.
        On subsequent runs, reuses the saved session cookies.
        
        Args:
            profile_url: Full LinkedIn profile URL (e.g. https://www.linkedin.com/in/username)
            
        Returns:
            Dict with source_type, source_file, full_text (structured profile text)
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise ImportError(
                "playwright is required for LinkedIn web scraping but is not installed.\n"
                "Install the full dependencies:\n"
                "    pip install -r requirements-full.txt\n"
                "    playwright install chromium"
            ) from exc

        # Normalize URL
        profile_url = profile_url.rstrip("/")
        if not profile_url.startswith("http"):
            profile_url = f"https://www.linkedin.com/in/{profile_url}"

        logger.info(f"Scraping LinkedIn profile: {profile_url}")

        _browser_args = [
            "--disable-blink-features=AutomationControlled",
            "--ignore-certificate-errors",
        ]

        with sync_playwright() as p:
            # Use persistent context to save/reuse login session
            # If the session is corrupted, clear it and retry once
            try:
                context = p.chromium.launch_persistent_context(
                    user_data_dir=str(SESSION_DIR),
                    headless=False,  # Must be visible for initial login
                    args=_browser_args,
                )
            except Exception as e:
                logger.warning(f"Browser session corrupted, clearing and retrying: {e}")
                if SESSION_DIR.exists():
                    shutil.rmtree(SESSION_DIR, ignore_errors=True)
                context = p.chromium.launch_persistent_context(
                    user_data_dir=str(SESSION_DIR),
                    headless=False,
                    args=_browser_args,
                )

            page = context.pages[0] if context.pages else context.new_page()

            # Navigate to profile and wait for page to settle
            # Don't use networkidle — LinkedIn never stops loading resources
            page.goto(profile_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(5000)  # Let JS redirects (authwall) settle

            # Check if we hit the authwall or login page (after all redirects)
            current_url = page.url
            logger.info(f"Page loaded at: {current_url}")
            needs_login = (
                "/login" in current_url
                or "authwall" in current_url
                or "/signup" in current_url
                or "/uas/" in current_url
                or "checkpoint" in current_url
            )

            if needs_login:
                logger.info("LinkedIn login required. Please log in manually in the browser window...")
                print("\n" + "=" * 60)
                print("LINKEDIN LOGIN REQUIRED")
                print("=" * 60)
                print("A browser window has opened. Please:")
                print("  1. Log into your LinkedIn account")
                print("  2. Wait for the page to fully load")
                print("  3. The scraper will continue automatically")
                print("=" * 60 + "\n")

                # Wait for the user to log in — URL path must be /in/ or /feed (not query params)
                try:
                    page.wait_for_url(
                        lambda url: self._is_logged_in_url(url),
                        timeout=180000,  # 3 minutes to log in
                    )
                except Exception:
                    context.close()
                    raise RuntimeError(
                        "Login timed out after 3 minutes. Run again and log in when the browser opens."
                    )

                # Give it time to load after login
                page.wait_for_timeout(3000)

                # Re-navigate to the target profile after login
                if "/in/" not in page.url or profile_url.split("/in/")[-1] not in page.url:
                    page.goto(profile_url, wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(3000)

            else:
                pass  # Already waited for networkidle above

            # Scroll down to load ALL lazy-loaded sections (experience, education, skills)
            for _ in range(10):
                page.keyboard.press("End")
                page.wait_for_timeout(1000)

            # Get the page content and parse it BEFORE closing the browser
            html = page.content()
            final_url = page.url

            # Validate we actually got a profile page, not authwall
            if "authwall" in final_url or "/login" in final_url or "/signup" in final_url:
                context.close()
                raise RuntimeError(
                    "LinkedIn blocked access — could not get past the login page. "
                    "Run the command again and log in when the browser opens."
                )

            # Parse the HTML while browser is still open
            profile_data = self._parse_profile_html(html)

            # Check if we got meaningful profile data (raw text with section headers)
            raw_text = profile_data.get("_raw_text", "")
            profile_keywords = ["experience", "education", "skills", "about", "projects"]
            keyword_hits = sum(1 for kw in profile_keywords if kw.lower() in raw_text.lower())
            has_profile_content = len(raw_text) > 200 and keyword_hits >= 1

            # If no meaningful content, the session may be stale — ask user to log in
            if not has_profile_content:
                logger.info("No profile sections found — session may be stale. Prompting for login...")
                print("\n" + "=" * 60)
                print("LINKEDIN LOGIN REQUIRED")
                print("=" * 60)
                print("The browser loaded but couldn't access full profile data.")
                print("Your saved session may have expired. Please:")
                print("  1. Log into your LinkedIn account in the browser")
                print("  2. Navigate to " + profile_url)
                print("  3. The scraper will continue automatically")
                print("=" * 60 + "\n")

                # Navigate to login page so user can log in
                page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded", timeout=30000)

                try:
                    page.wait_for_url(
                        lambda url: self._is_logged_in_url(url),
                        timeout=180000,  # 3 minutes
                    )
                except Exception:
                    context.close()
                    raise RuntimeError(
                        "Login timed out after 3 minutes. Run again and log in when the browser opens."
                    )

                # After login, navigate to the target profile
                page.wait_for_timeout(3000)
                page.goto(profile_url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(3000)

                # Scroll again to load ALL lazy sections
                for _ in range(10):
                    page.keyboard.press("End")
                    page.wait_for_timeout(1000)

                # Re-capture the page content
                html = page.content()
                profile_data = self._parse_profile_html(html)

            context.close()

        # Final validation — check raw text has profile-like content
        raw = profile_data.get("_raw_text", "")
        profile_keywords = ["experience", "education", "skills", "about", "projects"]
        keyword_hits = sum(1 for kw in profile_keywords if kw.lower() in raw.lower())
        has_data = len(raw) > 200 and keyword_hits >= 1

        if not has_data:
            raise RuntimeError(
                "LinkedIn scraping completed but no meaningful profile data was found. "
                "This usually means LinkedIn blocked access or the session expired. "
                "Try deleting the .linkedin_session folder and running again."
            )

        profile_text = self._profile_to_text(profile_data, profile_url)
        logger.info(f"Scraped profile: {profile_data.get('name', 'Unknown')} — "
                     f"{len(raw)} chars of profile text")

        return {
            "source_type": "linkedin",
            "source_file": f"linkedin:{profile_url}",
            "full_text": profile_text,
        }

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

    def _parse_profile_html(self, html: str) -> Dict[str, Any]:
        """
        Parse LinkedIn profile HTML into structured data.
        
        Strategy: Extract ALL visible text from the profile page and let the
        LLM parser handle structuring. LinkedIn's DOM changes frequently, so
        we avoid relying on specific CSS selectors.
        """
        soup = BeautifulSoup(html, "html.parser")
        data = {
            "name": "",
            "headline": "",
            "location": "",
            "about": "",
            "experiences": [],
            "education": [],
            "skills": [],
        }

        # Name — h1 is stable across LinkedIn versions
        name_el = soup.select_one("h1")
        if name_el:
            data["name"] = name_el.get_text(strip=True)

        # Remove script, style, nav, header, footer, and other non-content elements
        for tag in soup.find_all(["script", "style", "noscript", "svg", "img",
                                   "nav", "header", "footer", "aside", "code"]):
            tag.decompose()

        # Remove hidden elements
        for tag in soup.find_all(attrs={"aria-hidden": "true"}):
            # Keep aria-hidden spans inside main content — LinkedIn uses these for visible text
            pass

        # Extract text from main content area
        main = soup.find("main") or soup.find("body")
        if not main:
            return data

        # Walk through all text-bearing elements and build a clean text representation
        seen_texts = set()
        lines = []
        for el in main.find_all(["h1", "h2", "h3", "h4", "span", "p", "li", "div", "a"]):
            # Skip elements that have child elements with text (avoid duplication)
            # Only get direct text or text from leaf-level elements
            text = el.get_text(separator=" ", strip=True)
            if not text or len(text) < 3:
                continue
            # Skip very long blobs (probably entire section duplicates)
            if len(text) > 500:
                continue
            # Dedup
            if text in seen_texts:
                continue
            seen_texts.add(text)
            lines.append(text)

        # Build the raw text — this is what the LLM will parse
        raw_text = "\n".join(lines)

        # Try to detect section boundaries to help the LLM
        # LinkedIn section headers are usually: Experience, Education, Skills, etc.
        section_keywords = ["experience", "education", "skills", "licenses & certifications",
                           "projects", "volunteer", "honors", "publications", "languages",
                           "about", "summary"]
        has_sections = sum(1 for kw in section_keywords if kw in raw_text.lower())

        if has_sections >= 1:
            # We have real profile content
            data["_raw_text"] = raw_text
            logger.info(f"Extracted {len(lines)} text elements from profile page "
                        f"({has_sections} section headers detected)")
        else:
            logger.warning("No profile sections found in extracted text")
            data["_raw_text"] = raw_text if len(raw_text) > 100 else ""

        return data

    def _profile_to_text(self, data: Dict, url: str) -> str:
        """Convert parsed profile data into a text summary for the parser agent."""
        lines = []

        if data.get("name"):
            lines.append(f"Name: {data['name']}")
        lines.append(f"LinkedIn: {url}")
        lines.append("")

        # The raw text contains the full profile content — experience, education, skills, etc.
        if data.get("_raw_text"):
            lines.append(data["_raw_text"])

        return "\n".join(lines)
