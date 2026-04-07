import logging
import json
from typing import Dict, Any
from docling.document_converter import DocumentConverter

logger = logging.getLogger(__name__)

class LinkedInIngestor:
    def __init__(self):
        self.converter = DocumentConverter()

    def ingest(self, file_path: str) -> Dict[str, Any]:
        """
        Ingests a LinkedIn PDF profile.
        LinkedIn PDFs have a specific structure, but Docling handles them well as generic docs.
        """
        logger.info(f"Ingesting LinkedIn Profile: {file_path}")
        result = self.converter.convert(file_path)
        
        if result.status != "success":
            raise RuntimeError(f"Docling failed: {result.errors}")

        doc = result.document
        markdown_text = doc.export_to_markdown()
        
        # We return the raw markdown. 
        # The AI Parser will be responsible for extracting "Experience" vs "Skills" from this text
        # because LinkedIn PDFs formatting varies.
        return {
            "source_type": "linkedin",
            "source_file": str(file_path),
            "full_text": markdown_text
        }
