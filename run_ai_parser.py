
import argparse
import json
import os
import sys
from dotenv import load_dotenv
from resume_ingest_docling import ingest_resume
from ai_resume_parser import AIResumeParser

def main():
    # Load environment variables from .env file if present
    load_dotenv()
    parser = argparse.ArgumentParser(description="Run AI Resume Parser")
    parser.add_argument("resume_path", help="Path to resume file (PDF/DOCX)")
    parser.add_argument("--out", default="ai_parsed_resume.json", help="Output JSON path")
    parser.add_argument("--api-key", help="OpenAI API Key (optional)")
    parser.add_argument("--model", default="gpt-4o-mini", help="OpenAI Model (default: gpt-4o-mini)")
    parser.add_argument("--mock", action="store_true", help="Use mock LLM client for testing")
    
    args = parser.parse_args()
    
    # 1. Ingest
    print(f"Ingesting {args.resume_path}...")
    try:
        raw_json = ingest_resume(args.resume_path)
    except Exception as e:
        print(f"Error during ingestion: {e}")
        # Build a dummy structure if ingestion fails just to test the AI parser? 
        # No, that defeats the purpose.
        sys.exit(1)
        
    # 2. Setup LLM Client
    llm_client = None
    if args.mock:
        print("Using Mock LLM Client...")
        class MockResponse:
            def __init__(self, content):
                self.choices = [type('obj', (object,), {'message': type('obj', (object,), {'content': content})})]

        class MockClient:
            def __init__(self):
                self.chat = type('obj', (object,), {'completions': type('obj', (object,), {'create': self.create})})
            def create(self, **kwargs):
                # Return dummy JSON based on prompt
                msgs = kwargs.get("messages", [])
                user_msg = msgs[-1]["content"] if msgs else ""
                
                if "Extract projects" in user_msg:
                    return MockResponse(json.dumps({"projects": [{"name": "Mock Project", "bullets": ["Did stuff"], "source": [0]}]}))
                elif "skill" in user_msg.lower():
                    return MockResponse(json.dumps({"skills": [{"name": "Mock Python", "source": [0]}]}))
                elif "experience" in user_msg.lower():
                    return MockResponse(json.dumps({"experience": [{"role_header": "Dev, Mock Co", "bullets": ["Worked"], "source": [0]}]}))
                elif "education" in user_msg.lower():
                    return MockResponse(json.dumps({"education": [{"institution": "Mock Uni", "degrees": ["BS"], "source": [0]}]}))
                return MockResponse("{}")

        llm_client = MockClient()
        
    elif args.api_key or os.environ.get("OPENAI_API_KEY"):
        try:
            from openai import OpenAI
            # If args.api_key is provided, use it. Otherwise OpenAI() auto-detects env var.
            if args.api_key:
                llm_client = OpenAI(api_key=args.api_key)
            else:
                llm_client = OpenAI()
        except ImportError:
             print("OpenAI library not installed. Please install it or use --mock.")
             sys.exit(1)
        except Exception as e:
             print(f"Failed to initialize OpenAI client: {e}")
             sys.exit(1)
            
    # 3. AI Parse
    print("Running AI Parser...")
    parser = AIResumeParser(raw_json, llm_client=llm_client, model_name=args.model)
    structured_data = parser.parse()
    
    # 4. Output
    with open(args.out, "w") as f:
        json.dump(structured_data, f, indent=2)
        
    print(f"Success! Output written to {args.out}")

if __name__ == "__main__":
    main()
