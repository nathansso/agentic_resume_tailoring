"""
resume_ingest_docling.py

End-to-end resume ingestion using Docling v2.

- Converts a resume (PDF or DOCX) using Docling
- Explores the DoclingDocument tree
- Heuristically detects sections (Education, Experience, Projects, Skills)
- Groups bullet points correctly
- Outputs a canonical JSON schema suitable for downstream agents

Designed for Agent 1 (Knowledge Graph Builder).
"""

from collections import defaultdict
import json
from typing import Dict, List

from docling.document_converter import DocumentConverter


# -----------------------------
# Configuration
# -----------------------------

SECTION_KEYWORDS = {
    "education": "education",
    "projects": "projects",
    "experience": "experience",
    "skills": "skills",
    "technical skills": "skills",
}

OUTPUT_SCHEMA_VERSION = "1.0"


# -----------------------------
# Utilities
# -----------------------------

def is_section_header(text: str) -> bool:
    """Return True if text is a known section header."""
    t = text.strip().lower()
    # Direct match or broad match
    if t in SECTION_KEYWORDS:
        return True
    
    # Check for common variations
    for key in SECTION_KEYWORDS:
        if key in t and len(t) < 30: # e.g. "Professional Experience" contains "experience"
            return True
            
    return False

def normalize_section(text: str) -> str:
    t = text.strip().lower()
    if t in SECTION_KEYWORDS:
        return SECTION_KEYWORDS[t]
    
    # Map variation to canonical
    for key, canonical in SECTION_KEYWORDS.items():
        if key in t:
            return canonical
    return "uncategorized"


# -----------------------------
# Core Parsing Logic
# -----------------------------

def extract_sections(doc) -> Dict[str, List[Dict]]:
    """
    Iterate through DoclingDocument items and group them under sections.
    Uses a state-machine approach since many resumes do not have semantic headings.
    Now also captures source IDs (item index) for evidence tracking.
    """

    sections = defaultdict(list)
    current_section = "uncategorized" # Default for header info / ambiguities

    for i, (item, _) in enumerate(doc.iterate_items()):
        if not hasattr(item, "text"):
            continue

        text = item.text.strip()
        if not text:
            continue

        # Detect section header
        if is_section_header(text):
            current_section = normalize_section(text)
            # Store the header itself as well, or just switch context?
            # We'll switch context.
            continue

        # Append content under current section with ID
        sections[current_section].append({
            "type": type(item).__name__,
            "text": text,
            "id": i  # Source evidence ID
        })

    return sections


# -----------------------------
# Section-Specific Parsers
# -----------------------------

def parse_bullets(items: List[Dict]) -> Dict[str, List]:
    """
    Group ListItem + subsequent TextItem into coherent bullet strings.
    Returns a dict with 'texts' (list of strings) and 'ids' (list of lists of source IDs).
    """
    bullets = []
    bullet_ids = []
    
    current_text = ""
    current_ids = []

    for item in items:
        # If it's a new list item, flush buffer
        if item["type"] == "ListItem":
            if current_text:
                bullets.append(current_text.strip())
                bullet_ids.append(current_ids)
            current_text = item["text"]
            current_ids = [item["id"]]
        else:
            # Append text to current buffer
            # Treat "TextItem" as continuation if we are in a bullet, or separate if not?
            # Existing logic was: buffer += " " + item["text"]
            if current_text:
                 current_text += " " + item["text"]
                 current_ids.append(item["id"])
            else:
                 # If we see text without a preceding ListItem, treat as standalone bullet or preamble?
                 # For now, treat as new bullet to be safe
                 current_text = item["text"]
                 current_ids = [item["id"]]

    if current_text:
        bullets.append(current_text.strip())
        bullet_ids.append(current_ids)

    # Return structured object
    return {
        "texts": bullets, 
        "source_ids": bullet_ids
    }


def parse_projects(items: List[Dict]) -> List[Dict]:
    projects = []
    current = None
    
    # Simple heuristic: bold text (not easily detected here without styles) 
    # or just TextItem followed by ListItems.
    
    for item in items:
        # Start a new project if we hit a TextItem after finishing previous bullets, 
        # OR if it's the first item. 
        # (This is a weak heuristic, AI parser will improve this).
        if item["type"] == "TextItem" and (current is None or len(item["text"]) < 50): 
            # Assuming short text implies title
            current = {
                "name": item["text"],
                "bullets": [],
                "source": [item["id"]]
            }
            projects.append(current)
        elif current:
            current["bullets"].append(item)
            current["source"].append(item["id"])
        else:
            # Fallback: Create a generic project if we start with bullets
            current = {
                "name": "Miscellaneous Projects",
                "bullets": [item],
                "source": [item["id"]]
            }
            projects.append(current)

    # Normalize bullets
    final_projects = []
    for proj in projects:
        parsed = parse_bullets(proj["bullets"])
        # We want: { name, bullets: [strings], source: [ids] }
        # Re-map structure
        final_projects.append({
            "name": proj["name"],
            "bullets": parsed["texts"],
            "bullet_sources": parsed["source_ids"], # Granular sources
            "source": proj["source"] # Aggregate source
        })

    return final_projects


def parse_experience(items: List[Dict]) -> List[Dict]:
    roles = []
    current = None

    for item in items:
        text = item["text"]
        
        # Heuristic: role/company line contains a comma and date range
        if "," in text and any(y in text for y in ["20", "19"]):
            current = {
                "role_header": text,
                "bullets": [],
                "source": [item["id"]]
            }
            roles.append(current)
        elif current:
            current["bullets"].append(item)
            current["source"].append(item["id"])
        else:
             # loose text before first role
             pass

    final_roles = []
    for role in roles:
        parsed = parse_bullets(role["bullets"])
        final_roles.append({
            "role_header": role["role_header"],
            "bullets": parsed["texts"],
            "bullet_sources": parsed["source_ids"],
            "source": role["source"]
        })

    return final_roles


def parse_education(items: List[Dict]) -> List[Dict]:
    education = []
    current = None

    for item in items:
        text = item["text"]

        if "university" in text.lower() or "college" in text.lower():
            current = {
                "institution": text,
                "details": [],
                "source": [item["id"]]
            }
            education.append(current)
        elif current:
            current["details"].append(text)
            current["source"].append(item["id"])

    return education


def parse_skills(items: List[Dict]) -> List[Dict]:
    # Changed return type to list of dicts with source tracking
    skills_list = []
    current_category = None
    
    for item in items:
        text = item["text"]
        
        if text.endswith(":"):
            current_category = text[:-1]
            # We don't necessarily extract the category as a skill, but context
        else:
            # Split by comma
            raw_skills = [s.strip() for s in text.split(",") if s.strip()]
            for s in raw_skills:
                skills_list.append({
                    "name": s,
                    "category": current_category,
                    "source": [item["id"]]
                })

    return skills_list


# -----------------------------
# Main Ingest Function
# -----------------------------

def ingest_resume(path: str) -> Dict:
    converter = DocumentConverter()
    result = converter.convert(path)

    if result.status != "success":
        raise RuntimeError(f"Docling conversion failed: {result.errors}")

    doc = result.document
    sections = extract_sections(doc)

    all_raw_items = []
    for i, (item, _) in enumerate(doc.iterate_items()):
        if hasattr(item, "text"):
            all_raw_items.append({
                "type": type(item).__name__,
                "text": item.text,
                "id": i
            })

    parsed = {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "source": "resume",
        "education": parse_education(sections.get("education", [])),
        "projects": parse_projects(sections.get("projects", [])),
        "experience": parse_experience(sections.get("experience", [])),
        "skills": parse_skills(sections.get("skills", [])),
        "uncategorized": sections.get("uncategorized", []), 
        "all_raw_items": all_raw_items, # Full context for AI parser
    }

    return parsed


# -----------------------------
# CLI Entry Point
# -----------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Ingest a resume using Docling")
    parser.add_argument("path", help="Path to resume PDF or DOCX")
    parser.add_argument("--out", default="parsed_resume.json")

    args = parser.parse_args()

    data = ingest_resume(args.path)

    with open(args.out, "w") as f:
        json.dump(data, f, indent=2)

    print(f"Parsed resume written to {args.out}")
