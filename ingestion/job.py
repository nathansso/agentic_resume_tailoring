import logging
import json
from typing import Dict, Any

logger = logging.getLogger(__name__)

class JobIngestor:
    def __init__(self):
        pass

    def ingest(self, text: str = None, file_path: str = None) -> Dict[str, Any]:
        """
        Ingests a JD from text or file.
        Returns a dict with 'raw_text'.
        Parsing into 'skills' happens in the Parser/Agent layer.
        """
        raw_text = ""
        if file_path:
            with open(file_path, "r", encoding="utf-8") as f:
                raw_text = f.read()
        elif text:
            raw_text = text
        else:
            raise ValueError("Must provide text or file_path")

        # Basic cleanup
        raw_text = raw_text.strip()
        
        return {
            "source_type": "job_description",
            "raw_text": raw_text
        }
