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


MAX_CACHED_MATERIALS = 2
MAX_DOCUMENT_CHUNKS = 400
_materials: OrderedDict[str, StudyMaterial] = OrderedDict()
_materials_lock = RLock()
MAX_RETRIEVAL_DISTANCE = 1.3


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

        try:
            pages = extract_pages_from_file(document.storage_path)
            if not any(text.strip() for _, text in pages):
                raise ValueError("No readable text was found.")
            chunks, page_numbers = _chunks_with_pages(pages)
            if not chunks:
                raise ValueError("No text chunks were produced.")
            material = StudyMaterial(
                document_id=document.id,
                owner_id=document.user_id,
                filename=document.filename,
                file_path=document.storage_path,
                size=document.file_size,
                chunks=chunks,
                page_numbers=page_numbers,
                index=create_index_for_chunks(chunks, create_embeddings),
            )
            _remember_material(material)
            return material
        except (OSError, ValueError):
            logger.warning("Could not load indexed study material %s.", document.id)
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

            top_k = min(3, len(material.chunks))
            distances, indices = search(
                material.index,
                query_embedding,
                top_k=top_k,
            )
            for distance, chunk_index in zip(distances[0], indices[0]):
                if (
                    0 <= chunk_index < len(material.chunks)
                    and float(distance) <= MAX_RETRIEVAL_DISTANCE
                ):
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
