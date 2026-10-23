import logging
import os
import time
import uuid
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
        req_id = uuid.uuid4().hex[:8]
        start_time = time.monotonic()
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
                    candidate_start = time.monotonic()
                    response = http_client.post(url, headers=headers, json=payload)
                    candidate_duration = round(time.monotonic() - candidate_start, 3)
                    logger.info(
                        "[Groq Req %s] model=%s status=%s elapsed=%ss",
                        req_id,
                        candidate,
                        response.status_code,
                        candidate_duration,
                    )
                    if response.status_code == 401:
                        logger.error(
                            "[Groq Req %s] Authentication failed (401). Verify GROQ_API_KEY.",
                            req_id,
                        )
                        raise LLMProviderError(
                            "EduGuide could not authenticate with the AI provider. Please verify GROQ_API_KEY.",
                            status_code=502,
                        )
                    if response.status_code == 429:
                        logger.warning(
                            "[Groq Req %s] Rate limit reached (429) on model %s.",
                            req_id,
                            candidate,
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
                                "[Groq Req %s] Model %s not available (%s); trying next candidate...",
                                req_id,
                                candidate,
                                response.status_code,
                            )
                            continue
                    if response.status_code >= 500:
                        logger.warning(
                            "[Groq Req %s] Server error (%s) on model %s; trying next candidate...",
                            req_id,
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
                    total_time = round(time.monotonic() - start_time, 3)
                    logger.info(
                        "[Groq Req %s] Completed successfully with model %s in %ss",
                        req_id,
                        candidate,
                        total_time,
                    )
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


def ask_llm(
    question,
    context="",
    college_context="",
    history=None,
    materials_selected=False,
):
    system_prompt = """You are EduGuide, an expert AI academic companion and tutor for college students.

Your goal is to provide crystal-clear, pedagogically sound, and student-friendly explanations that are engaging, easy to read, and effective for learning and exam revision.

Operational Modes and Rules:

Mode A: Selected Study Material Grounding (When study materials are selected and relevant excerpts are provided)
- Treat the selected study material as the primary and authoritative source.
- Answer the student's exact question faithfully using the provided excerpts.
- Cite the exact source and page number inline or at the end of each key point using the format: `[Source: <filename>, p. <page_number>]`.
- If the document contains only part of the answer, explain what is supported by the document and state what is missing.
- NEVER fabricate citations, page numbers, author quotations, or false details. Never claim a citation that retrieval did not provide.

Mode B: Selected Study Material Missing Information (When study materials are selected but excerpts do not contain the answer)
- State clearly and honestly in your opening sentence:
  "The selected study material does not contain information about [topic]."
- If relevant excerpts are absent, do NOT claim the document supports an answer.
- Then, provide a clearly separated general explanation under a distinct markdown heading:
  "### General Academic Explanation"

Mode C: General Academic Mode (When NO study materials are selected)
- Provide a comprehensive, accurate academic explanation directly answering the question using fundamental subject principles.
- Do NOT fabricate document citations or claim the information came from an uploaded PDF.

Mode D: Follow-Up Questions and Continuity
- Use conversation history to resolve pronouns and references (e.g., "what is its formula?", "explain this step", "give an example", "continue").
- Do not let conversation history override the selected source when the student asks about a specific document.

Mode E: Source Provenance and Verification Questions
- If the student asks questions like "Was this answer extracted from my PDF?", "Which document did you use?", or "Where did you get that answer?":
  * Explain the exact evidence: state the filename(s) and page number(s) that were retrieved and used, or clarify that it was answered using general academic knowledge.
  * Do not give generic citation advice; state directly what grounded the previous answer.

Mathematical Formulas & Code:
- Always write mathematical equations using clean LaTeX. Use `$formula$` for inline math and `$$...$$` on separate lines for display math.
- For code: Use fenced code blocks with language identifiers.
"""

    context_str = str(context).strip() if context else ""
    college_context_str = str(college_context).strip() if college_context else ""

    if materials_selected:
        if context_str:
            material_block = f"Retrieved excerpts from selected study materials:\n{context_str}"
        else:
            material_block = (
                "[Notice: The student selected study materials for this question, but no matching "
                "or relevant excerpts were found in the uploaded documents. Explicitly state that the "
                "selected study material does not contain this information, then provide a general "
                "academic explanation under a '### General Academic Explanation' heading.]"
            )
    else:
        material_block = "[No study materials were selected for this question. Answer directly from general academic knowledge.]"

    college_block = (
        f"Retrieved official college website excerpts:\n{college_context_str}"
        if college_context_str
        else ""
    )

    prompt_parts = []
    if material_block:
        prompt_parts.append(material_block)
    if college_block:
        prompt_parts.append(college_block)

    prompt_parts.append(f"Student question:\n{question}")
    prompt_parts.append(
        "Start with the answer. Answer the student's exact question directly. "
        "Format any mathematical expressions using standard LaTeX with $...$ for inline math "
        "and $$...$$ on separate lines for display math."
    )

    user_prompt = "\n\n".join(prompt_parts)

    messages = [{"role": "system", "content": system_prompt}]

    # Include recent conversation history if provided
    if history:
        for turn in history:
            role = turn.get("role")
            content = turn.get("content")
            if role in {"user", "assistant"} and content and str(content).strip():
                messages.append({"role": role, "content": str(content).strip()})

    messages.append({"role": "user", "content": user_prompt})

    response = client.chat.completions.create(
        messages=messages,
        temperature=0.3,
        timeout=45.0,
    )
    return response.choices[0].message.content
