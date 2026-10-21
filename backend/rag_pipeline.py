
from functools import lru_cache
from pathlib import Path
from threading import Lock

from document_processor import extract_text_from_pdf
from chunker import create_chunks
from embeddings import create_embeddings
from index_cache import create_index_for_chunks
from retriever import search
from llm_service import ask_llm
from college_knowledge import retrieve_college_context
from study_materials import retrieve_context_with_sources


BASE_DIR = Path(__file__).resolve().parent
PDF_PATH = BASE_DIR / "data" / "documents" / "BDA 2 marks.pdf"
_academic_index_lock = Lock()


@lru_cache(maxsize=1)
def load_academic_index():
    """Build the academic document index only when first needed."""
    with _academic_index_lock:
        if not PDF_PATH.exists():
            return [], None

        text = extract_text_from_pdf(str(PDF_PATH))
        chunks = create_chunks(text)

        if not chunks:
            return [], None

        index = create_index_for_chunks(chunks, create_embeddings)
        return chunks, index


COLLEGE_KEYWORDS = {
    "sairam",
    "college",
    "campus",
    "department",
    "departments",
    "faculty",
    "hod",
    "admission",
    "admissions",
    "placement",
    "placements",
    "hostel",
    "fees",
    "fee",
    "circular",
    "principal",
    "autonomous",
    "anna university",
    "regulation",
    "regulations",
    "exam",
    "exams",
    "coe",
    "transport",
    "bus",
    "library",
    "curriculum",
}


def answer_question(
    question: str,
    document_ids=None,
    owner_id=None,
    include_sources=False,
    db=None,
    history=None,
):
    # Only retrieve institution-specific website context if the question touches college topics
    is_college_query = any(keyword in question.lower() for keyword in COLLEGE_KEYWORDS)
    if is_college_query:
        college_context, college_sources = retrieve_college_context(question, db)
    else:
        college_context, college_sources = "", []

    if document_ids:
        if owner_id is None:
            raise ValueError(
                "Document retrieval requires an authenticated owner."
            )

        personal_context, personal_sources = retrieve_context_with_sources(
            question,
            document_ids,
            owner_id,
            db=db,
        )

        answer = ask_llm(
            question,
            personal_context,
            college_context=college_context,
            history=history,
            materials_selected=True,
        )
        sources = personal_sources + college_sources
        return (answer, sources) if include_sources else answer

    # When no study materials are selected, answer directly from model's general academic knowledge
    # without contaminating the prompt with unrelated sample documents
    answer = ask_llm(
        question,
        context="",
        college_context=college_context,
        history=history,
        materials_selected=False,
    )

    return (answer, college_sources) if include_sources else answer
