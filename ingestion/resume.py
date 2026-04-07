import logging
import json
from pathlib import Path
from typing import Dict, List, Any
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
            "full_text": full_text
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
