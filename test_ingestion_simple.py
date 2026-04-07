import logging
from ingestion.resume import ResumeIngestor
# from ingestion.linkedin import LinkedInIngestor
# from ingestion.github import GitHubIngestor

logging.basicConfig(level=logging.INFO)

def test_resume():
    # Test with nate_resume.docx if exists, or just print
    path = "nate_resume.docx"
    try:
        ingestor = ResumeIngestor()
        data = ingestor.ingest(path)
        print("Resume Ingestion Success:")
        print(f"Sections found: {list(data['parsed_sections'].keys())}")
    except Exception as e:
        print(f"Resume Ingestion Failed (expected if file missing): {e}")

if __name__ == "__main__":
    test_resume()
