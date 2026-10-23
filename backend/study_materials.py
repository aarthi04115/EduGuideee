import logging
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from threading import RLock

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from chunker import create_chunks
from document_processor import extract_pages_from_file
from embeddings import create_embeddings
from index_cache import create_index_for_chunks
from models import Document
from retriever import search


logger = logging.getLogger(__name__)


@dataclass
class StudyMaterial:
    document_id: str
    owner_id: str
    filename: str
    file_path: str
    size: int
    chunks: list[str]
    page_numbers: list[int]
    index: object

    def as_dict(self, status="indexed", created_at=None):
        result = {
            "id": self.document_id,
            "filename": self.filename,
            "size": self.size,
            "status": status,
        }
        if created_at is not None:
            result["created_at"] = created_at.isoformat()
        return result


MAX_CACHED_MATERIALS = 4
MAX_DOCUMENT_CHUNKS = 1200
_materials: OrderedDict[str, StudyMaterial] = OrderedDict()
_materials_lock = RLock()
# FAISS IndexFlatL2 returns squared Euclidean distance: ||u - v||^2
# For normalized vectors, ||u - v||^2 = 2 - 2*cos(theta).
# Threshold of 1.55 squared distance corresponds to cosine similarity of 0.225 (L2 distance ~1.24).
MAX_RETRIEVAL_DISTANCE = 1.55


def _remember_material(material):
    with _materials_lock:
        _materials[material.document_id] = material
        _materials.move_to_end(material.document_id)
        while len(_materials) > MAX_CACHED_MATERIALS:
            _materials.popitem(last=False)


def _chunks_with_pages(pages):
    chunks = []
    page_numbers = []
    for page_number, text in pages:
        page_chunks = create_chunks(text)
        chunks.extend(page_chunks)
        page_numbers.extend([page_number] * len(page_chunks))
    return chunks, page_numbers


def add_material(document_id, owner_id, filename, file_path, pages, size):
    chunks, page_numbers = _chunks_with_pages(pages)
    if not chunks:
        raise ValueError("The document does not contain readable text.")

    if len(chunks) > MAX_DOCUMENT_CHUNKS:
        logger.info(
            "Document has %d chunks; capping to %d for index stability.",
            len(chunks),
            MAX_DOCUMENT_CHUNKS,
        )
        chunks = chunks[:MAX_DOCUMENT_CHUNKS]
        page_numbers = page_numbers[:MAX_DOCUMENT_CHUNKS]

    index = create_index_for_chunks(chunks, create_embeddings)
    material = StudyMaterial(
        document_id=document_id,
        owner_id=owner_id,
        filename=filename,
        file_path=str(Path(file_path).resolve()),
        size=size,
        chunks=chunks,
        page_numbers=page_numbers,
        index=index,
    )
    _remember_material(material)
    return material


def initialize_materials(db: Session):
    return (
        db.scalar(
            select(func.count(Document.id)).where(Document.status == "indexed")
        )
        or 0
    )


def get_material(document_id, owner_id):
    with _materials_lock:
        material = _materials.get(document_id)
        if material is None or material.owner_id != owner_id:
            return None
        _materials.move_to_end(document_id)
        return material


def load_material(document: Document, owner_id: str):
    if document.user_id != owner_id or document.status != "indexed":
        return None

    with _materials_lock:
        material = get_material(document.id, owner_id)
        if material is not None:
            return material

        pages = None
        # 1. Try reading from storage_path on disk
        if document.storage_path and Path(document.storage_path).is_file():
            try:
                pages = extract_pages_from_file(document.storage_path)
            except Exception as exc:
                logger.warning("Could not extract pages from disk for %s: %s", document.id, exc)

        # 2. If disk file is unavailable (e.g. Render container restart), restore from DB pages_json
        if not pages and getattr(document, "pages_json", None):
            try:
                pages = [
                    (int(item.get("page_number", idx + 1)), str(item.get("text", "")))
                    for idx, item in enumerate(document.pages_json)
                    if isinstance(item, dict)
                ]
            except Exception as exc:
                logger.warning("Could not parse pages_json for %s: %s", document.id, exc)

        # 3. Fallback to document.content_text
        if not pages and getattr(document, "content_text", None):
            pages = [(1, document.content_text)]

        if not pages or not any(text.strip() for _, text in pages):
            logger.warning("Could not load or restore readable text for study material %s.", document.id)
            return None

        try:
            chunks, page_numbers = _chunks_with_pages(pages)
            if not chunks:
                return None
            if len(chunks) > MAX_DOCUMENT_CHUNKS:
                chunks = chunks[:MAX_DOCUMENT_CHUNKS]
                page_numbers = page_numbers[:MAX_DOCUMENT_CHUNKS]

            material = StudyMaterial(
                document_id=document.id,
                owner_id=document.user_id,
                filename=document.filename,
                file_path=document.storage_path or "",
                size=document.file_size,
                chunks=chunks,
                page_numbers=page_numbers,
                index=create_index_for_chunks(chunks, create_embeddings),
            )
            _remember_material(material)
            return material
        except Exception:
            logger.exception("Failed to load indexed study material %s.", document.id)
    return None


def list_materials(owner_id, db: Session):
    documents = db.scalars(
        select(Document)
        .where(Document.user_id == owner_id)
        .order_by(Document.created_at.desc())
    )
    return [
        {
            "id": document.id,
            "filename": document.filename,
            "size": document.file_size,
            "status": document.status,
            "created_at": document.created_at.isoformat(),
        }
        for document in documents
    ]


def remove_material(document_id):
    with _materials_lock:
        return _materials.pop(document_id, None)


def retrieve_context(question, document_ids, owner_id):
    context, _ = retrieve_context_with_sources(question, document_ids, owner_id)
    return context


import re
from collections import Counter


def _keyword_overlap(query: str, text: str) -> float:
    query_terms = set(re.findall(r"\b[a-zA-Z0-9_]{3,}\b", query.lower()))
    if not query_terms:
        return 0.0
    text_words = re.findall(r"\b[a-zA-Z0-9_]{3,}\b", text.lower())
    if not text_words:
        return 0.0
    counts = Counter(text_words)
    overlap = sum(counts[term] for term in query_terms if term in counts)
    return float(overlap)


def retrieve_context_with_sources(question, document_ids, owner_id, db=None):
    query_embedding = create_embeddings([question])
    context_parts = []
    sources = []
    with _materials_lock:
        for document_id in document_ids:
            material = get_material(document_id, owner_id)
            if material is None and db is not None:
                document = db.scalar(
                    select(Document).where(
                        Document.id == document_id,
                        Document.user_id == owner_id,
                        Document.status == "indexed",
                    )
                )
                if document is not None:
                    material = load_material(document, owner_id)
            if material is None:
                raise ValueError("A selected study material is not available.")

            total_chunks = len(material.chunks)
            if total_chunks == 0:
                continue

            # 1. Semantic vector search
            top_k_vector = min(8, total_chunks)
            distances, indices = search(
                material.index,
                query_embedding,
                top_k=top_k_vector,
            )

            # Map chunk_idx -> score (higher is better)
            scored_candidates: dict[int, float] = {}

            for dist, idx in zip(distances[0], indices[0]):
                if 0 <= idx < total_chunks:
                    sq_dist = float(dist)
                    if sq_dist <= MAX_RETRIEVAL_DISTANCE:
                        # Convert squared distance to similarity score in [0, 1]
                        sim = max(0.0, 1.0 - (sq_dist / 2.0))
                        scored_candidates[idx] = sim * 1.5

            # 2. Keyword lexical overlap boost
            for idx, chunk_text in enumerate(material.chunks):
                kw_score = _keyword_overlap(question, chunk_text)
                if kw_score > 0:
                    current_score = scored_candidates.get(idx, 0.0)
                    boost = min(kw_score * 0.3, 2.0)
                    scored_candidates[idx] = current_score + boost

            # 3. Filter candidates: must have meaningful semantic match (within MAX_RETRIEVAL_DISTANCE) or keyword overlap
            valid_candidates = {
                idx: score
                for idx, score in scored_candidates.items()
                if score >= 0.35
            }

            # Sort best candidates and select top chunks (up to 5 per document)
            best_chunk_indices = sorted(
                valid_candidates.keys(),
                key=lambda i: valid_candidates[i],
                reverse=True,
            )[:5]

            # Preserve chronological/reading order for context coherence
            for chunk_index in sorted(best_chunk_indices):
                page_number = material.page_numbers[chunk_index]
                context_parts.append(
                    f"Study material: {material.filename}, page {page_number}\n"
                    f"{material.chunks[chunk_index]}"
                )
                source = {
                    "id": material.document_id,
                    "filename": material.filename,
                    "page_number": page_number,
                }
                if source not in sources:
                    sources.append(source)
    return "\n\n".join(context_parts), sources

