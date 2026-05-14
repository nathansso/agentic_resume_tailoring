
import os
import sys
from dotenv import load_dotenv

def test_auth():
    print("Loading environment variables...")
    load_dotenv()
    
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("ERROR: OPENAI_API_KEY not found in environment variables.")
        print("Please ensure you have a .env file in this directory with 'OPENAI_API_KEY=your-key'.")
        return False
        
    print(f"API Key found: {api_key[:8]}...{api_key[-4:]}")
    
    print("Attempting to initialize OpenAI client...")
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        
        print("Sending test request to OpenAI API...")
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "user", "content": "Hello, are you working?"}
            ],
            max_tokens=10
        )
        print("Success! Response received:")
        print(response.choices[0].message.content)
        return True
    except Exception as e:
        print(f"Authentication failed or API error: {e}")
        return False

if __name__ == "__main__":
    if test_auth():
        print("\nAuthentication checks passed.")
        sys.exit(0)
    else:
        print("\nAuthentication checks FAILED.")
        sys.exit(1)
