import asyncio
import logging
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import func, inspect, select
from sqlalchemy.exc import IntegrityError, OperationalError, ProgrammingError, SQLAlchemyError
from sqlalchemy.orm import Session

from auth import router as auth_router
from database import Base, SessionLocal, engine, get_db
from models import AuthSession, Conversation, Document, Message, User
from rag_pipeline import answer_question
from schemas import (
    ChatRequest,
    ConversationDocumentAttach,
    ConversationCreate,
    ConversationResponse,
    ConversationUpdate,
    MessageResponse,
    conversation_response,
    message_response,
)
from security import csrf_protection, get_current_user
from study_materials import (
    add_material,
    initialize_materials,
    load_material,
    list_materials,
    remove_material,
)


logger = logging.getLogger(__name__)
BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = Path(
    os.getenv("EDUGUIDE_UPLOAD_DIR", str(BASE_DIR / "data" / "uploads"))
).resolve()
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
ALLOWED_CONTENT_TYPES = {
    ".pdf": {"application/pdf", "application/octet-stream"},
    ".txt": {"text/plain", "application/octet-stream"},
}


def _check_db_startup():
    if engine is None:
        logger.warning("DATABASE_URL is not configured; database-backed endpoints are unavailable.")
        return
    try:
        with engine.connect() as connection:
            connection.execute(select(1))
            existing_tables = set(inspect(connection).get_table_names())
        missing_tables = set(Base.metadata.tables) - existing_tables
        if missing_tables:
            logger.warning(
                "Database is reachable, but required tables are missing: %s. "
                "Apply Alembic migrations before using database-backed endpoints.",
                ", ".join(sorted(missing_tables)),
            )
        else:
            with SessionLocal() as db:
                document_count = initialize_materials(db)
                logger.info(
                    "Found %s indexed study materials; indexes will load on demand.",
                    document_count,
                )
    except Exception as exc:
        logger.warning(
            "Database startup check failed or deferred (%s); endpoints will connect dynamically.",
            exc,
        )


@asynccontextmanager
async def lifespan(_app):
    try:
        await asyncio.wait_for(asyncio.to_thread(_check_db_startup), timeout=5.0)
    except Exception as exc:
        logger.warning("Database startup check timed out or deferred (%s); server starting immediately.", exc)
    yield


configured_origins = {
    origin.strip().rstrip("/")
    for origin in os.getenv(
        "EDUGUIDE_CORS_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173,https://eduguide-frontend.onrender.com",
    ).split(",")
    if origin.strip()
}
configured_origins.add("https://eduguide-frontend.onrender.com")
configured_origins.add("http://localhost:5173")
configured_origins.add("http://127.0.0.1:5173")

app = FastAPI(title="EduGuide API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=sorted(configured_origins),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)
app.include_router(auth_router)


@app.exception_handler(OperationalError)
async def database_connection_error_handler(
    _request: Request, error: OperationalError
):
    logger.exception("Database connection or operation failed.")
    return JSONResponse(
        status_code=503,
        content={
            "detail": "The database is temporarily unavailable. Please try again."
        },
    )


@app.exception_handler(ProgrammingError)
async def database_programming_error_handler(
    _request: Request, error: ProgrammingError
):
    logger.exception("A database query could not be executed.")
    error_code = getattr(error.orig, "sqlstate", None) or getattr(
        error.orig, "pgcode", None
    )
    message = str(error.orig).lower()
    if error_code == "42P01" or "no such table" in message or "does not exist" in message:
        return JSONResponse(
            status_code=503,
            content={
                "detail": "The database schema is not initialized. Apply the pending database migrations."
            },
        )
    return JSONResponse(
        status_code=500,
        content={"detail": "The database could not complete this request."},
    )


@app.exception_handler(IntegrityError)
async def database_integrity_error_handler(
    _request: Request, error: IntegrityError
):
    logger.exception("A database integrity constraint rejected an operation.")
    return JSONResponse(
        status_code=409,
        content={"detail": "The requested change conflicts with existing data."},
    )


@app.exception_handler(SQLAlchemyError)
async def database_error_handler(_request: Request, error: SQLAlchemyError):
    logger.exception("A database operation failed.")
    return JSONResponse(
        status_code=500,
        content={"detail": "The database could not complete this request."},
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_request: Request, error: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "detail": [
                {
                    "loc": list(issue["loc"]),
                    "msg": issue["msg"],
                    "type": issue["type"],
                }
                for issue in error.errors()
            ]
        },
    )


def _conversation_or_404(db: Session, conversation_id: str, user_id: str):
    conversation = db.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == user_id,
        )
    )
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return conversation


def _document_response(document):
    return {
        "id": document.id,
        "filename": document.filename,
        "size": document.file_size,
        "status": document.status,
        "created_at": document.created_at.isoformat(),
    }


def _message_sequence(db: Session, conversation_id: str):
    return (
        db.scalar(
            select(func.coalesce(func.max(Message.sequence_number), 0)).where(
                Message.conversation_id == conversation_id
            )
        )
        or 0
    )


@app.get("/")
def home():
    return {"message": "EduGuide backend is running"}


@app.get("/health/ready")
def readiness(db: Session = Depends(get_db)):
    db.execute(select(1))
    existing_tables = set(inspect(db.get_bind()).get_table_names())
    missing_tables = set(Base.metadata.tables) - existing_tables
    if missing_tables:
        raise HTTPException(
            status_code=503,
            detail="The database schema is not initialized. Apply the pending database migrations.",
        )
    return {"status": "ready", "database": "available"}


@app.get("/conversations", response_model=list[ConversationResponse])
def get_conversations(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    conversations = db.scalars(
        select(Conversation)
        .where(Conversation.user_id == user.id)
        .order_by(Conversation.updated_at.desc(), Conversation.id)
    )
    return [conversation_response(conversation) for conversation in conversations]


@app.post(
    "/conversations",
    response_model=ConversationResponse,
    status_code=201,
    dependencies=[Depends(csrf_protection)],
)
def create_conversation(
    data: ConversationCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    now = datetime.now(timezone.utc)
    conversation = Conversation(
        id=str(uuid.uuid4()),
        user_id=user.id,
        title=(data.title or "New conversation").strip()[:120] or "New conversation",
        created_at=now,
        updated_at=now,
    )
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return conversation_response(conversation)


@app.get(
    "/conversations/{conversation_id}/messages",
    response_model=list[MessageResponse],
)
def get_conversation_messages(
    conversation_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    conversation = _conversation_or_404(db, conversation_id, user.id)
    messages = db.scalars(
        select(Message)
        .where(Message.conversation_id == conversation.id)
        .order_by(Message.sequence_number)
    )
    return [message_response(message) for message in messages]


@app.patch(
    "/conversations/{conversation_id}",
    response_model=ConversationResponse,
    dependencies=[Depends(csrf_protection)],
)
def rename_conversation(
    conversation_id: str,
    data: ConversationUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    conversation = _conversation_or_404(db, conversation_id, user.id)
    conversation.title = data.title
    conversation.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(conversation)
    return conversation_response(conversation)


@app.delete(
    "/conversations/{conversation_id}",
    status_code=204,
    dependencies=[Depends(csrf_protection)],
)
def delete_conversation(
    conversation_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    conversation = _conversation_or_404(db, conversation_id, user.id)
    db.delete(conversation)
    db.commit()


@app.post("/chat", dependencies=[Depends(csrf_protection)])
def chat(
    request: ChatRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    question = request.question.strip()
    if not question:
        raise HTTPException(status_code=422, detail="Please enter a question.")
    requested_document_ids = (
        list(dict.fromkeys(request.document_ids))
        if request.document_ids is not None
        else None
    )

    is_new_conversation = request.conversation_id is None
    if is_new_conversation:
        conversation = Conversation(
            id=str(uuid.uuid4()),
            user_id=user.id,
            title=question[:80],
        )
        db.add(conversation)
        db.flush()
        sequence = 0
    else:
        conversation = _conversation_or_404(
            db,
            request.conversation_id,
            user.id,
        )
        conversation = db.scalar(
            select(Conversation)
            .where(Conversation.id == conversation.id)
            .with_for_update()
        )
        sequence = _message_sequence(db, conversation.id)

    selected_documents = []
    if requested_document_ids is None:
        selected_documents = list(
            db.scalars(
                select(Document)
                .join(Document.conversations)
                .where(
                    Conversation.id == conversation.id,
                    Document.user_id == user.id,
                    Document.status == "indexed",
                )
            )
        )
    elif requested_document_ids:
        selected_documents = list(
            db.scalars(
                select(Document).where(
                    Document.id.in_(requested_document_ids),
                    Document.user_id == user.id,
                    Document.status == "indexed",
                )
            )
        )
        if len(selected_documents) != len(requested_document_ids):
            raise HTTPException(
                status_code=404,
                detail="A selected study material is not available.",
            )
        for document in selected_documents:
            if all(
                associated.id != document.id
                for associated in conversation.documents
            ):
                conversation.documents.append(document)

    document_ids = [document.id for document in selected_documents]
    if any(
        load_material(document, user.id) is None
        for document in selected_documents
    ):
        raise HTTPException(
            status_code=404,
            detail="A selected study material is not available.",
        )

    latest_message = db.scalar(
        select(Message)
        .where(Message.conversation_id == conversation.id)
        .order_by(Message.sequence_number.desc())
        .limit(1)
    )
    reusing_failed_question = (
        latest_message is not None
        and latest_message.role == "user"
        and latest_message.content == question
    )
    if not reusing_failed_question:
        if sequence == 0 and not is_new_conversation:
            conversation.title = question[:80]
        db.add(
            Message(
                id=str(uuid.uuid4()),
                conversation_id=conversation.id,
                role="user",
                content=question,
                sequence_number=sequence + 1,
            )
        )
        conversation.updated_at = datetime.now(timezone.utc)
        db.commit()
        sequence += 1
    else:
        db.commit()

    try:
        answer_result = answer_question(
            question,
            document_ids or None,
            owner_id=user.id,
            include_sources=True,
            db=db,
        )
        if isinstance(answer_result, tuple):
            answer, sources = answer_result
        else:
            answer, sources = answer_result, []
        if not answer:
            raise RuntimeError("The answer provider returned an empty response.")
    except Exception as error:
        logger.error("Answer generation failed (%s).", type(error).__name__)
        raise HTTPException(
            status_code=502,
            detail={
                "message": "EduGuide could not generate an answer. Your question was saved; you can retry.",
                "conversation_id": conversation.id,
            },
        ) from error

    assistant_message = Message(
        id=str(uuid.uuid4()),
        conversation_id=conversation.id,
        role="assistant",
        content=answer,
        sequence_number=sequence + 1,
        sources=sources,
    )
    conversation.updated_at = datetime.now(timezone.utc)
    db.add(assistant_message)
    db.commit()

    return {
        "question": question,
        "answer": answer,
        "conversation_id": conversation.id,
        "sources": sources,
    }


@app.get("/documents")
def documents(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return {"documents": list_materials(user.id, db)}


@app.get("/conversations/{conversation_id}/documents")
def get_conversation_documents(
    conversation_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    conversation = _conversation_or_404(db, conversation_id, user.id)
    documents = db.scalars(
        select(Document)
        .join(Document.conversations)
        .where(
            Conversation.id == conversation.id,
            Document.user_id == user.id,
        )
        .order_by(Document.created_at, Document.id)
    )
    return [_document_response(document) for document in documents]


@app.post(
    "/conversations/{conversation_id}/documents",
    dependencies=[Depends(csrf_protection)],
)
def attach_document_to_conversation(
    conversation_id: str,
    request: ConversationDocumentAttach,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    conversation = _conversation_or_404(db, conversation_id, user.id)
    document = db.scalar(
        select(Document).where(
            Document.id == request.document_id,
            Document.user_id == user.id,
            Document.status == "indexed",
        )
    )
    if document is None or load_material(document, user.id) is None:
        raise HTTPException(status_code=404, detail="Study material not found.")
    if all(associated.id != document.id for associated in conversation.documents):
        conversation.documents.append(document)
        conversation.updated_at = datetime.now(timezone.utc)
        db.commit()
    return {
        "id": document.id,
        "filename": document.filename,
        "size": document.file_size,
        "status": document.status,
        "created_at": document.created_at.isoformat(),
    }


@app.delete(
    "/conversations/{conversation_id}/documents/{document_id}",
    status_code=204,
    dependencies=[Depends(csrf_protection)],
)
def detach_document_from_conversation(
    conversation_id: str,
    document_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    conversation = _conversation_or_404(db, conversation_id, user.id)
    document = next(
        (
            item
            for item in conversation.documents
            if item.id == document_id and item.user_id == user.id
        ),
        None,
    )
    if document is None:
        raise HTTPException(status_code=404, detail="Study material not found.")
    conversation.documents.remove(document)
    conversation.updated_at = datetime.now(timezone.utc)
    db.commit()


@app.post(
    "/documents/upload",
    status_code=201,
    dependencies=[Depends(csrf_protection)],
)
async def upload_document(
    file: UploadFile = File(...),
    conversation_id: str | None = Form(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    original_name = (file.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    extension = Path(original_name).suffix.lower()
    if extension not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=415,
            detail="Unsupported file type. Upload a PDF or TXT file.",
        )
    if file.content_type not in ALLOWED_CONTENT_TYPES[extension]:
        raise HTTPException(
            status_code=415,
            detail="The file content type does not match a supported format.",
        )

    if conversation_id:
        conversation = _conversation_or_404(db, conversation_id, user.id)
    else:
        now = datetime.now(timezone.utc)
        conversation = Conversation(
            id=str(uuid.uuid4()),
            user_id=user.id,
            title=f"Study material: {original_name[:95]}",
            created_at=now,
            updated_at=now,
        )
        db.add(conversation)
        db.flush()

    document_id = str(uuid.uuid4())
    stored_path = UPLOAD_DIR / f"{document_id}{extension}"
    size = 0
    prefix = b""
    indexed_material = None

    def cleanup_failed_upload():
        if indexed_material is not None:
            remove_material(document_id)
        try:
            stored_path.unlink(missing_ok=True)
        except OSError as error:
            logger.error(
                "Failed to clean up a rejected study material upload (%s).",
                type(error).__name__,
            )

    try:
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        with stored_path.open("xb") as destination:
            while True:
                content = await file.read(64 * 1024)
                if not content:
                    break
                prefix = (prefix + content)[:5]
                size += len(content)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail="The file is too large. The maximum size is 10 MB.",
                    )
                destination.write(content)

        if size == 0:
            raise HTTPException(status_code=422, detail="The uploaded file is empty.")
        if extension == ".pdf" and not prefix.startswith(b"%PDF-"):
            raise HTTPException(
                status_code=422,
                detail="The uploaded file is not a valid PDF.",
            )
        try:
            from document_processor import extract_pages_from_file

            pages = extract_pages_from_file(stored_path)
        except (UnicodeDecodeError, OSError, ValueError) as error:
            raise HTTPException(
                status_code=422,
                detail="EduGuide could not read this file. Check that it is not damaged or password-protected.",
            ) from error
        if (
            not any(text.strip() for _, text in pages)
            or any("\x00" in text for _, text in pages)
        ):
            raise HTTPException(
                status_code=422,
                detail="No readable text was found in this document.",
            )

        indexed_material = add_material(
            document_id,
            user.id,
            original_name,
            stored_path,
            pages,
            size,
        )
        document = Document(
            id=document_id,
            user_id=user.id,
            filename=original_name[:255],
            storage_path=str(stored_path),
            status="indexed",
            file_size=size,
        )
        conversation.documents.append(document)
        conversation.updated_at = datetime.now(timezone.utc)
        db.add(document)
        db.commit()
        db.refresh(document)
        response = {
            "id": document.id,
            "filename": document.filename,
            "size": document.file_size,
            "status": document.status,
            "created_at": document.created_at.isoformat(),
            "conversation_id": conversation.id,
        }
        return response
    except HTTPException:
        db.rollback()
        cleanup_failed_upload()
        raise
    except SQLAlchemyError:
        db.rollback()
        cleanup_failed_upload()
        raise
    except OSError as error:
        db.rollback()
        cleanup_failed_upload()
        logger.error("Study material upload failed (%s).", type(error).__name__)
        raise HTTPException(
            status_code=500,
            detail="The document could not be saved. Please try again.",
        ) from error
    except Exception as error:
        db.rollback()
        cleanup_failed_upload()
        logger.error("Study material indexing failed (%s).", type(error).__name__)
        raise HTTPException(
            status_code=500,
            detail="The document could not be indexed. Please try again.",
        ) from error
    finally:
        await file.close()


@app.delete(
    "/documents/{document_id}",
    status_code=204,
    dependencies=[Depends(csrf_protection)],
)
def delete_document(
    document_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    document = db.scalar(
        select(Document).where(
            Document.id == document_id,
            Document.user_id == user.id,
        )
    )
    if document is None:
        raise HTTPException(status_code=404, detail="Study material not found.")

    storage_path = Path(document.storage_path).resolve()
    if storage_path.parent != UPLOAD_DIR:
        logger.error("Refusing to delete a study material outside the upload directory.")
        raise HTTPException(status_code=500, detail="The study material could not be removed.")
    staged_path = storage_path.with_name(f"{storage_path.name}.deleting")
    try:
        if storage_path.exists():
            storage_path.replace(staged_path)
        db.delete(document)
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        if staged_path.exists():
            staged_path.replace(storage_path)
        raise
    except OSError as error:
        db.rollback()
        if staged_path.exists():
            staged_path.replace(storage_path)
        logger.exception("Study material removal failed.")
        raise HTTPException(
            status_code=500,
            detail="The study material could not be removed. Please try again.",
        ) from error

    remove_material(document_id)
    try:
        staged_path.unlink(missing_ok=True)
    except OSError:
        logger.exception("A deleted study material file could not be cleaned up.")
        raise HTTPException(
            status_code=500,
            detail="The study material was removed from your account but its stored file could not be cleaned up.",
        )
