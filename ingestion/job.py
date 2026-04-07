import logging
import json
from typing import Dict, Any
from pathlib import Path

logger = logging.getLogger(__name__)

class JobIngestor:
    def __init__(self):
        pass

    def ingest(self, text: str = None, file_path: str = None) -> Dict[str, Any]:
        """
        Ingests a JD from text or file.
        Returns a dict with 'raw_text' and 'source'.
        Parsing into structured skills happens in the job_analyzer agent.
        """
        raw_text = ""
        source = "direct_input"

        if file_path:
            path = Path(file_path)
            if not path.exists():
                raise FileNotFoundError(f"Job description file not found: {file_path}")
            with open(path, "r", encoding="utf-8") as f:
                raw_text = f.read()
            source = str(path.name)
        elif text:
            raw_text = text
        else:
            raise ValueError("Must provide text or file_path")

        raw_text = raw_text.strip()
        if not raw_text:
            raise ValueError("Job description is empty")

        return {
            "source_type": "job_description",
            "source": source,
            "raw_text": raw_text,
        }
