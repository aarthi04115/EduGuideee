import logging
import os
from pathlib import Path
from dotenv import load_dotenv
import httpx

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")
load_dotenv(Path(__file__).resolve().parent / ".env")


def ask_llm(question, context, college_context=""):
    system_prompt = """
You are EduGuide, an intelligent academic doubt-support assistant
for students.

Your goal is to help students understand academic concepts clearly.

Follow these rules:
1. Answer the student's actual question directly in clear, student-friendly language.
2. When relevant retrieved study material is provided, use it as the primary
   source and explain the supported concepts accurately.
3. If the retrieved material is absent, irrelevant, or insufficient, use
   reliable general academic knowledge when appropriate.
4. Never imply that general knowledge came from the student's material.
   Do not invent quotations, page numbers, citations, or source details.
5. State a limitation briefly only when it materially affects the answer.
6. If you are uncertain about a fact, say so instead of inventing it.
7. Use examples, equations, code, or steps when they help explain the answer.
8. Stay focused on education and academic learning.
9. Only state institution-specific facts about Sri Sairam Engineering
   College when supported by the official college excerpts below. If those
   excerpts do not support the requested fact, say that you could not verify
   it from the indexed official pages and suggest checking https://sairam.edu.in/.
"""

    user_prompt = f"""
Retrieved personal study-material excerpts (use only when relevant):
{context if context and str(context).strip() else "[No relevant excerpts were retrieved.]"}

Retrieved official college website excerpts:
{college_context if college_context and str(college_context).strip() else "[No relevant official college excerpts were retrieved.]"}

Student question:
{question}

Start with the answer. Use the retrieved excerpts as evidence only for claims
they support; otherwise answer from general academic knowledge.
"""

    raw_key = os.getenv("GROQ_API_KEY", "")
    api_key = "".join(raw_key.split()).strip("'\"")
    if not api_key:
        raise RuntimeError(
            "GROQ_API_KEY is not set. Add your Groq API key to the project environment variables."
        )

    configured_model = "".join(os.getenv("GROQ_MODEL", "openai/gpt-oss-120b").split()).strip("'\"")
    candidate_models = [
        configured_model,
        "openai/gpt-oss-120b",
        "openai/gpt-oss-20b",
        "llama-3.3-70b-versatile",
    ]
    seen = set()
    models_to_try = [m for m in candidate_models if not (m in seen or seen.add(m))]

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    last_error = None
    with httpx.Client(timeout=45.0) as client:
        for model in models_to_try:
            try:
                response = client.post(
                    url,
                    headers=headers,
                    json={
                        "model": model,
                        "messages": messages,
                        "temperature": 0.3,
                    },
                )
                if response.status_code == 404 or "model_not_found" in response.text:
                    logger.warning("Groq model %s not available, trying next candidate...", model)
                    continue
                response.raise_for_status()
                data = response.json()
                return data["choices"][0]["message"]["content"]
            except Exception as exc:
                last_error = exc
                error_str = str(exc).lower()
                if "model_not_found" in error_str or "does not exist" in error_str or "404" in error_str:
                    continue
                raise

    if last_error:
        raise last_error
