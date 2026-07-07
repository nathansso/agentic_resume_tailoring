import logging
import json
import re
from pathlib import Path
from typing import Dict, List, Any, Optional

from ingestion.document_text import extract_text_lightweight

logger = logging.getLogger(__name__)

# Heuristic Mapping
SECTION_KEYWORDS = {
    "education": "education",
    "projects": "projects",
    "experience": "experience",
    "work experience": "experience",
    "employment": "experience",
    "skills": "skills",
    "technical skills": "skills",
    "certifications": "certifications"
}

# Header contact-line parsing (issue #75): pull actual values, not just field
# presence, so ingestion can backfill User.linkedin_url/github_username/etc.
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_LINKEDIN_RE = re.compile(r"(?:https?://)?(?:www\.)?linkedin\.com/in/([\w-]+)", re.I)
_GITHUB_RE = re.compile(r"(?:https?://)?(?:www\.)?github\.com/([\w.-]+)", re.I)
_PHONE_RE = re.compile(r"\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}")
_DOMAIN_RE = re.compile(r"^(?:https?://)?(?:www\.)?[\w-]+(?:\.[\w-]+)+(?:/\S*)?$", re.I)


def _parse_contact_part(part: str) -> tuple[str, str] | None:
    """Classify one header contact token, returning (kind, value) or None.

    *value* is the piece worth persisting: full URL for linkedin/portfolio,
    bare username for github, the raw string for email/phone/location.
    """
    md = _MD_LINK_RE.search(part)
    label, url = (md.group(1), md.group(2)) if md else (part, part)
    haystack = f"{label} {url}"

    m = _EMAIL_RE.search(haystack)
    if m:
        return "email", m.group()

    m = _LINKEDIN_RE.search(haystack)
    if m:
        value = url if _LINKEDIN_RE.search(url) else haystack
        u = value if value.startswith("http") else f"https://{value.lstrip('/')}"
        return "linkedin", u

    m = _GITHUB_RE.search(haystack)
    if m:
        return "github", m.group(1)

    if _PHONE_RE.search(part):
        return "phone", _PHONE_RE.search(part).group()

    if re.match(r"[A-Za-z].*,\s+[A-Z]{2}$", label.strip()):
        return "location", label.strip()

    if _DOMAIN_RE.match(url.strip()):
        u = url if url.startswith("http") else f"https://{url.lstrip('/')}"
        return "portfolio", u

    return None

def extract_style_profile(markdown_text: str) -> dict:
    """Heuristically extract resume style metadata from Docling markdown output."""
    lines = markdown_text.split("\n")
    section_order: List[str] = []
    section_labels: Dict[str, str] = {}
    header_lines: List[str] = []
    in_header = True
    bullet_counts: Dict[str, int] = {"-": 0, "*": 0, "•": 0}

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        heading_text = re.sub(r"^#{1,3}\s+", "", stripped)
        key = SECTION_KEYWORDS.get(heading_text.lower())
        if key:
            in_header = False
            if key not in section_order:
                section_order.append(key)
                section_labels[key] = heading_text
            continue
        if in_header:
            header_lines.append(stripped)
        for prefix, ch in [("- ", "-"), ("* ", "*"), ("• ", "•")]:
            if stripped.startswith(prefix):
                bullet_counts[ch] += 1

    contact_sep = " | "
    contact_fields: List[str] = []
    contact_values: Dict[str, str] = {}
    for line in header_lines[1:]:
        if " | " in line:
            contact_sep = " | "
        elif " · " in line:
            contact_sep = " · "
        elif " • " in line:
            contact_sep = " • "
        for part in re.split(r"\s*[|·•]\s*", line):
            p = part.strip()
            if not p:
                continue
            if "@" in p and "email" not in contact_fields:
                contact_fields.append("email")
            elif "linkedin" in p.lower() and "linkedin" not in contact_fields:
                contact_fields.append("linkedin")
            elif "github" in p.lower() and "github" not in contact_fields:
                contact_fields.append("github")
            elif re.search(r"\d{3}[-.\s]\d{3}", p) and "phone" not in contact_fields:
                contact_fields.append("phone")
            elif re.match(r"[A-Za-z].*,\s+[A-Z]{2}", p) and "location" not in contact_fields:
                contact_fields.append("location")

            parsed = _parse_contact_part(p)
            if parsed and parsed[0] not in contact_values:
                contact_values[parsed[0]] = parsed[1]

    top = max(bullet_counts, key=bullet_counts.get)
    bullet_prefix = f"{top} " if bullet_counts[top] > 0 else "- "

    return {
        "section_order": section_order,
        "section_labels": section_labels,
        "header": {
            "contact_separator": contact_sep,
            "contact_fields": contact_fields or ["email", "linkedin"],
            "contact_values": contact_values,
        },
        "bullet_prefix": bullet_prefix,
    }


class ResumeIngestor:
    def __init__(self):
        # docling loads lazily on first ingest — it pulls in PyTorch, which
        # OOM-kills low-memory deployments, so it is an optional dependency.
        self.converter = None

    def ingest(self, file_path: str) -> Dict[str, Any]:
        """
        Ingests a resume PDF/DOCX and returns a structured dictionary
        ready for the AI Parser or DB insertion.
        """
        logger.info(f"Ingesting resume: {file_path}")
        try:
            from docling.document_converter import DocumentConverter
        except ImportError:
            logger.info("docling not installed — using lightweight resume extraction")
            return self._ingest_lightweight(file_path)
        if self.converter is None:
            self.converter = DocumentConverter()
        result = self.converter.convert(file_path)

        if result.status != "success":
            raise RuntimeError(f"Docling failed: {result.errors}")

        doc = result.document
        sections = self._heuristic_segmentation(doc)
        
        # Flatten for "Raw" context if needed
        full_text = doc.export_to_markdown()

        return {
            "source_file": str(file_path),
            "parsed_sections": sections,
            "full_text": full_text,
            "resume_markdown": full_text,
            "resume_style": extract_style_profile(full_text),
        }

    def _ingest_lightweight(self, file_path: str) -> Dict[str, Any]:
        """Docling-free path: plain-text extraction + line-based segmentation."""
        full_text = extract_text_lightweight(file_path)
        return {
            "source_file": str(file_path),
            "parsed_sections": self._segment_text(full_text),
            "full_text": full_text,
            "resume_markdown": full_text,
            "resume_style": extract_style_profile(full_text),
        }

    def _segment_text(self, text: str) -> Dict[str, List[Dict]]:
        """Segment plain text into sections by matching heading keywords per line."""
        sections: Dict[str, List[Dict]] = {"uncategorized": []}
        current_section = "uncategorized"
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            heading_text = re.sub(r"^#{1,3}\s+", "", line)
            key = SECTION_KEYWORDS.get(heading_text.lower())
            if key:
                current_section = key
                sections.setdefault(current_section, [])
                continue
            sections.setdefault(current_section, []).append({"text": line, "type": "Text"})
        return sections

    def _heuristic_segmentation(self, doc) -> Dict[str, List[Dict]]:
        """
        Segment the Docling document into sections based on headers.
        """
        sections = {"uncategorized": []}
        current_section = "uncategorized"

        for item, level in doc.iterate_items():
            if not hasattr(item, "text"):
                continue
            
            text = item.text.strip()
            if not text:
                continue

            # Detect Header
            # Docling v2 usually marks headers well, but we check text too
            is_header = False
            if hasattr(item, "label") and item.label in ["section_header", "title", "Title", "SectionHeader"]:
                is_header = True
            
            # Text-based override
            lower_text = text.lower()
            if lower_text in SECTION_KEYWORDS:
                is_header = True
                norm_key = SECTION_KEYWORDS[lower_text]
            else:
                norm_key = None

            if is_header and norm_key:
                current_section = norm_key
                if current_section not in sections:
                    sections[current_section] = []
                # Don't add header to content? Or do? 
                # Let's skip adding the header string to the content list for cleaner data
                continue
            elif is_header and not norm_key:
                # Unknown header -> switch to uncategorized or keep previous?
                # Usually better to keep previous if it looks like a sub-header
                pass

            # Add content
            # We store the text and potentially the 'provenance' (doc structure)
            sections.setdefault(current_section, []).append({
                "text": text,
                "type": type(item).__name__
            })

        return sections

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("path")
    args = parser.parse_args()
    
    ingestor = ResumeIngestor()
    data = ingestor.ingest(args.path)
    print(json.dumps(data, indent=2))
