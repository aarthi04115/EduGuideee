import logging
import os
from pathlib import Path
from types import SimpleNamespace
from dotenv import load_dotenv
import httpx

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")
load_dotenv(Path(__file__).resolve().parent / ".env")


class LLMProviderError(Exception):
    def __init__(self, message, status_code=502):
        super().__init__(message)
        self.status_code = status_code


class GroqChatCompletions:
    def create(self, model=None, messages=None, temperature=0.3, timeout=45.0, **kwargs):
        raw_key = os.getenv("GROQ_API_KEY", "")
        api_key = "".join(raw_key.split()).strip("'\"")
        if not api_key:
            raise LLMProviderError(
                "GROQ_API_KEY is not configured. Add your Groq API key to the environment variables.",
                status_code=503,
            )

        configured_model = "".join(
            os.getenv("GROQ_MODEL", "openai/gpt-oss-120b").split()
        ).strip("'\"")
        requested_model = model or configured_model

        candidate_models = [
            requested_model,
            configured_model,
            "openai/gpt-oss-120b",
            "openai/gpt-oss-20b",
            "qwen/qwen3.8-27b",
        ]
        seen = set()
        models_to_try = [
            m for m in candidate_models if m and not (m in seen or seen.add(m))
        ]

        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept-Encoding": "gzip, deflate",
        }

        last_error = None
        with httpx.Client(
            timeout=timeout, headers={"Accept-Encoding": "gzip, deflate"}
        ) as http_client:
            for candidate in models_to_try:
                try:
                    payload = {
                        "model": candidate,
                        "messages": messages or [],
                        "temperature": temperature,
                    }
                    response = http_client.post(url, headers=headers, json=payload)
                    if response.status_code == 401:
                        logger.error(
                            "Groq authentication failed (401). Verify GROQ_API_KEY."
                        )
                        raise LLMProviderError(
                            "EduGuide could not authenticate with the AI provider. Please verify GROQ_API_KEY.",
                            status_code=502,
                        )
                    if response.status_code == 429:
                        logger.warning(
                            "Groq rate limit reached (429) on model %s.", candidate
                        )
                        raise LLMProviderError(
                            "AI provider rate limit exceeded. Please wait a moment before trying again.",
                            status_code=429,
                        )
                    if response.status_code in {400, 404}:
                        err_text = response.text.lower()
                        if (
                            "model_not_found" in err_text
                            or "does not exist" in err_text
                            or "not found" in err_text
                        ):
                            logger.warning(
                                "Groq model %s not available (%s); trying next candidate...",
                                candidate,
                                response.status_code,
                            )
                            continue
                    if response.status_code >= 500:
                        logger.warning(
                            "Groq server error (%s) on model %s; trying next candidate...",
                            response.status_code,
                            candidate,
                        )
                        last_error = LLMProviderError(
                            f"AI provider returned server error {response.status_code}.",
                            status_code=502,
                        )
                        continue

                    response.raise_for_status()
                    data = response.json()
                    content = data["choices"][0]["message"]["content"]
                    return SimpleNamespace(
                        choices=[
                            SimpleNamespace(
                                message=SimpleNamespace(content=content)
                            )
                        ]
                    )
                except (LLMProviderError,):
                    raise
                except httpx.TimeoutException as exc:
                    logger.warning(
                        "Groq request timed out on model %s (%s).",
                        candidate,
                        type(exc).__name__,
                    )
                    last_error = LLMProviderError(
                        "Request to the AI provider timed out. Please try again.",
                        status_code=504,
                    )
                    continue
                except httpx.ConnectError as exc:
                    logger.warning(
                        "Groq connection error on model %s (%s).",
                        candidate,
                        type(exc).__name__,
                    )
                    last_error = LLMProviderError(
                        "Connection to the AI provider failed. Please check network connectivity.",
                        status_code=502,
                    )
                    continue
                except Exception as exc:
                    last_error = exc
                    err_msg = str(exc).lower()
                    if "model_not_found" in err_msg or "does not exist" in err_msg:
                        continue
                    logger.exception(
                        "Unexpected error communicating with AI provider: %s",
                        type(exc).__name__,
                    )
                    raise

        if last_error:
            if isinstance(last_error, LLMProviderError):
                raise last_error
            raise LLMProviderError(
                f"AI provider request failed: {type(last_error).__name__}",
                status_code=502,
            ) from last_error
        raise LLMProviderError(
            "No candidate AI model was able to generate a response.", status_code=502
        )


class GroqChat:
    def __init__(self):
        self.completions = GroqChatCompletions()


class GroqClient:
    def __init__(self):
        self.chat = GroqChat()


# Client instance exposed for direct calls and testing mocks
client = GroqClient()


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

    response = client.chat.completions.create(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.3,
        timeout=45.0,
    )
    return response.choices[0].message.content
