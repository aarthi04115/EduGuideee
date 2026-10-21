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


def ask_llm(
    question,
    context="",
    college_context="",
    history=None,
    materials_selected=False,
):
    system_prompt = """You are EduGuide, an expert AI academic companion and tutor for college students.

Your goal is to provide crystal-clear, pedagogically sound, and student-friendly explanations that are engaging, easy to read, and effective for learning and exam revision.

Core Instruction Rules:
1. Student-Friendly Reading Structure:
   - Start immediately with the direct definition or answer to the student's question in the opening 1–2 sentences. Never start with conversational filler or disclaimers (e.g., "Certainly!", "Sure, I can help with that", "As an AI...").
   - Use clean, structured Markdown formatting that makes reading effortless for students:
     * Organize explanations with intuitive headings (e.g., `### Concept Overview`, `### Key Characteristics`, `### Step-by-Step Breakdown`, `### Example`, `### Key Takeaway`).
     * **Bold** key technical terms, formulas, and critical concepts so students can quickly scan and memorize them.
     * Use concise bullet points for properties, differences, or steps rather than dense walls of text.
     * Include concrete real-world examples, analogies, or practical applications to make abstract concepts tangible.
     * Provide a brief `### Key Takeaway` or summary box at the end for quick exam revision.
2. Mathematical Formula and Code Rendering:
   - Always write mathematical equations using clean, standard LaTeX syntax.
   - For inline formulas (variables, terms, brief equations), wrap them in single dollar signs: `$x_i$`, `$\\hat{y}$`, `$\\sigma^2$`, `$\\mathbb{E}[X]$`.
   - For block / standalone formulas and derivations, wrap them in double dollar signs on separate lines:
     $$
     \\mathbb{E}[(y - \\hat{f}(x))^2] = \\underbrace{\\text{Bias}(\\hat{f}(x))^2}_{\\text{Underfitting}} + \\underbrace{\\text{Var}(\\hat{f}(x))}_{\\text{Sensitivity}} + \\underbrace{\\sigma^2}_{\\text{Irreducible Error}}
     $$
   - Ensure commands like \\frac, \\sum, \\prod, \\int, \\mathbb, \\mathbf, \\hat, \\text, \\underbrace are valid LaTeX.
   - For code: Use fenced code blocks with the appropriate programming language identifier and informative comments.
3. Strict Study Material Grounding (When study materials are selected):
   - When relevant excerpts from selected study materials are provided, ground your answer primarily and faithfully in those excerpts. Prioritize the document's terminology, definitions, equations, and structure.
   - Anti-Hallucination: If the student selected study materials but the excerpts do NOT contain sufficient information to answer the question, state explicitly in the opening line:
     "The selected study material does not contain information about [topic]."
     Then provide a clearly separated general explanation under a markdown heading:
     "### General Academic Explanation"
   - NEVER fabricate citations, page numbers, author quotations, or false details. Never pretend that general external knowledge originated from the student's uploaded document.
4. General Academic Questions (When no study materials are selected):
   - Provide a comprehensive, accurate academic explanation directly answering the question using fundamental textbook principles in the subject.
5. Conversation History:
   - Use prior conversation messages to resolve pronouns, references, and follow-up contexts (e.g. "Explain that in more detail", "Give an example").
   - Do not let previous conversation topics override or distract from the new question.
6. Institutional Inquiries:
   - Only make factual claims about Sri Sairam Engineering College (admissions, campus, departments, dates) if verified in official college excerpts. If unverified, refer students to https://sairam.edu.in/.
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
