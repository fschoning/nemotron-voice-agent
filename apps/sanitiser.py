import os
import google.generativeai as genai

# Configure genai (assuming API key in env)
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

def sanitise_question(question: str, system_instruction: str) -> str:
    model = genai.GenerativeModel('gemini-2.5-flash', system_instruction=system_instruction)
    response = model.generate_content(question)
    return response.text.strip()
