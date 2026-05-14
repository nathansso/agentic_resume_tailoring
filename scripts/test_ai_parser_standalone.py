
import json
import logging
from ai_resume_parser import AIResumeParser

# Mock the LLM client behavior
class MockResponse:
    def __init__(self, content):
        self.choices = [type('obj', (object,), {'message': type('obj', (object,), {'content': content})})]

class MockClient:
    def __init__(self):
        self.chat = type('obj', (object,), {'completions': type('obj', (object,), {'create': self.create})})
    def create(self, **kwargs):
        msgs = kwargs.get("messages", [])
        user_msg = msgs[-1]["content"] if msgs else ""
        print(f"LLM Call Prompt Snippet: {user_msg[:100]}...")
        
        if "Extract projects" in user_msg:
            return MockResponse(json.dumps({"projects": [{"name": "AI Agent", "bullets": ["Built an agent"], "source": [10]}]}))
        elif "skill" in user_msg.lower():
            # Respond with a skill and cite source ID 5
            return MockResponse(json.dumps({"skills": [{"name": "Python", "source": [5]}]}))
        elif "experience" in user_msg.lower():
            return MockResponse(json.dumps({"experience": [{"role_header": "Engineer, Tech Co", "bullets": ["Coded stuff"], "source": [20]}]}))
        elif "education" in user_msg.lower():
            return MockResponse(json.dumps({"education": [{"institution": "MIT", "degrees": ["BS CS"], "source": [30]}]}))
        return MockResponse("{}")

def main():
    print("Generating synthetic input data...")
    # Synthetic output resembling resume_ingest_docling.py
    input_data = {
        "source": "synthetic_resume",
        "uncategorized": [
            {"text": "Python Developer with 5 years experience.", "id": 5, "type": "TextItem"},
            {"text": "Built a scalable AI agent using OpenAI API.", "id": 10, "type": "TextItem"},
        ],
        "experience": [
            {"role_header": "Software Engineer, Google (2020-Present)", 
             "bullets": ["Developed search algorithms."], 
             "bullet_sources": [[21]], "source": [20]}
        ],
        "education": [
            {"institution": "MIT", "details": ["BS Computer Science"], "source": [30]}
        ],
        "projects": [],
        "skills": []
    }
    
    print("Initializing AIResumeParser...")
    parser = AIResumeParser(input_data, llm_client=MockClient())
    
    print("Parsing...")
    output = parser.parse()
    
    print("\n--- Parsed Output ---")
    print(json.dumps(output, indent=2))
    
    # Assertions
    assert len(output["skills"]) > 0
    assert output["skills"][0]["name"] == "Python"
    assert output["skills"][0]["source"] == [5]
    print("\nVerification Passed: Skills extracted and linked to reference ID 5.")

if __name__ == "__main__":
    main()
