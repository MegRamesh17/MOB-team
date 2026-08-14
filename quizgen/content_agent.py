'''
Add content generation agent (PDFs -> readings + quizzes)

Uses Azure OpenAI to turn extracted PDF text into structured JSON
modules (readings + quiz questions) per role.

Needs real values in .env (from Key Vault):
- AZURE_OPENAI_ENDPOINT
- AZURE_OPENAI_KEY
- AZURE_OPENAI_CHAT_DEPLOYMENT

save_reading() / save_quiz() are still stubs - no DB writes yet,
pending Azure SQL schema. Also no company_id scoping yet since
we're single-company for the demo; will need to be added before
this generalizes to multiple companies.
'''

import os
import json
from openai import AzureOpenAI
from dotenv import load_dotenv
from pdf_extractor import extract_text_from_container

load_dotenv()

AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
AZURE_OPENAI_KEY = os.getenv("AZURE_OPENAI_KEY")
AZURE_OPENAI_CHAT_DEPLOYMENT = os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT")

client = AzureOpenAI(
    azure_endpoint=AZURE_OPENAI_ENDPOINT,
    api_key=AZURE_OPENAI_KEY,
    api_version="2024-06-01",
)


def generate_modules_from_text(role: str, source_text: str) -> dict:
    """
    Takes raw extracted text for a role's documents, and asks GPT to
    break it into structured readings + quiz questions, returned as JSON.
    """
    prompt = f"""
You are creating employee training content for the role: {role}.

Below is the raw source material (job description, SOPs, policies, etc.)
for this role. Break it into a set of digestible reading modules, and
for each module, write 2-3 quiz questions (multiple choice) testing
understanding of that module.

Return ONLY valid JSON in this exact structure, no other text:
{{
  "role": "{role}",
  "modules": [
    {{
      "title": "Module title",
      "content": "The reading content, written clearly for a new employee",
      "quiz": [
        {{
          "question": "Question text",
          "choices": ["A", "B", "C", "D"],
          "correct_answer": "A"
        }}
      ]
    }}
  ]
}}

Source material:
{source_text}
"""

    response = client.chat.completions.create(
        model=AZURE_OPENAI_CHAT_DEPLOYMENT,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
    )

    raw_output = response.choices[0].message.content
    return json.loads(raw_output)


def generate_modules_for_role(role: str, container_name: str) -> dict:
    """
    Full pipeline: pull all PDFs for a role's container from Blob Storage
    (via pdf_extractor), extract their text, and generate structured
    modules from it.
    """
    source_text = extract_text_from_container(container_name)
    return generate_modules_from_text(role, source_text)


# --- Persistence layer ---
# Stubbed until Azure SQL schema is finalized. Once ready, these will
# insert the generated modules/quizzes into the database instead of
# just returning them in memory.

def save_reading(title: str, content: str, role: str):
    """Placeholder — will insert a generated reading into the database."""
    pass

def save_quiz(reading_id: str, questions: list):
    """Placeholder — will insert generated quiz questions into the database."""
    pass