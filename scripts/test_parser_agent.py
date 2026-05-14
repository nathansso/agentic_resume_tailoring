import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import logging
from ingestion.resume import ResumeIngestor
from agents.parser import ResumeParserAgent
# Link parsing logging to stdout
logging.basicConfig(level=logging.INFO)

def test_pipeline():
    # 1. Ingest
    print("--- 1. Ingesting ---")
    ingestor = ResumeIngestor()
    # Try nate_resume.docx if local, or prompt error
    try:
        data = ingestor.ingest("nate_resume.docx")
        print("Ingestion OK.")
    except Exception as e:
        print(f"Ingestion failed: {e}")
        return

    # 2. Parse & Save
    print("--- 2. Parsing & Saving ---")
    agent = ResumeParserAgent()
    try:
        agent.parse_and_save(data)
        print("Parser finished.")
    except Exception as e:
        print(f"Parser failed: {e}")

if __name__ == "__main__":
    test_pipeline()
