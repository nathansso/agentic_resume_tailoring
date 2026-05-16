import logging
import json
import re
from pathlib import Path
from typing import Dict, List, Any, Optional
from docling.document_converter import DocumentConverter

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
    for line in header_lines[1:]:
        if " | " in line:
            contact_sep = " | "
        elif " · " in line:
            contact_sep = " · "
        elif " • " in line:
            contact_sep = " • "
        for part in re.split(r"\s*[|·•]\s*", line):
            p = part.strip()
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

    top = max(bullet_counts, key=bullet_counts.get)
    bullet_prefix = f"{top} " if bullet_counts[top] > 0 else "- "

    return {
        "section_order": section_order,
        "section_labels": section_labels,
        "header": {
            "contact_separator": contact_sep,
            "contact_fields": contact_fields or ["email", "linkedin"],
        },
        "bullet_prefix": bullet_prefix,
    }


class ResumeIngestor:
    def __init__(self):
        self.converter = DocumentConverter()

    def ingest(self, file_path: str) -> Dict[str, Any]:
        """
        Ingests a resume PDF/DOCX and returns a structured dictionary
        ready for the AI Parser or DB insertion.
        """
        logger.info(f"Ingesting resume: {file_path}")
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
