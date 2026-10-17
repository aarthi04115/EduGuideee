from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import relationship

from database import Base


conversation_documents = Table(
    "conversation_documents",
    Base.metadata,
    Column(
        "conversation_id",
        String(36),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "document_id",
        String(36),
        ForeignKey("documents.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Index("ix_conversation_documents_document", "document_id"),
)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("email", name="uq_users_email"),
        CheckConstraint("email = lower(email)", name="ck_users_email_normalized"),
    )

    id = Column(String(36), primary_key=True)
    name = Column(String(100), nullable=False)
    email = Column(String(254), nullable=False)
    password_hash = Column(String(255), nullable=False)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    conversations = relationship(
        "Conversation", back_populates="user", cascade="all, delete-orphan"
    )
    documents = relationship(
        "Document", back_populates="user", cascade="all, delete-orphan"
    )
    sessions = relationship(
        "AuthSession", back_populates="user", cascade="all, delete-orphan"
    )


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        Index("ix_conversations_user_updated", "user_id", "updated_at"),
    )

    id = Column(String(36), primary_key=True)
    user_id = Column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    title = Column(String(120), nullable=False)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    user = relationship("User", back_populates="conversations")
    messages = relationship(
        "Message",
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.created_at, Message.id",
    )
    documents = relationship(
        "Document",
        secondary=conversation_documents,
        back_populates="conversations",
    )


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint(
            "role IN ('user', 'assistant')",
            name="ck_messages_role",
        ),
        UniqueConstraint(
            "conversation_id",
            "sequence_number",
            name="uq_messages_conversation_sequence",
        ),
    )

    id = Column(String(36), primary_key=True)
    conversation_id = Column(
        String(36),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    role = Column(String(16), nullable=False)
    content = Column(Text, nullable=False)
    sequence_number = Column(Integer, nullable=False)
    sources = Column(JSON, nullable=False, default=list, server_default="[]")
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    conversation = relationship("Conversation", back_populates="messages")


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint(
            "status IN ('indexing', 'indexed', 'failed')",
            name="ck_documents_status",
        ),
        Index("ix_documents_user_created", "user_id", "created_at"),
    )

    id = Column(String(36), primary_key=True)
    user_id = Column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    filename = Column(String(255), nullable=False)
    storage_path = Column(String(1024), nullable=False)
    status = Column(
        String(16), nullable=False, default="indexing"
    )
    file_size = Column(Integer, nullable=False)
    content_text = Column(Text, nullable=True)
    pages_json = Column(JSON, nullable=True)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    user = relationship("User", back_populates="documents")
    conversations = relationship(
        "Conversation",
        secondary=conversation_documents,
        back_populates="documents",
    )


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    __table_args__ = (
        UniqueConstraint("token_id", name="uq_auth_sessions_token_id"),
        Index("ix_auth_sessions_user_expires", "user_id", "expires_at"),
    )

    id = Column(String(36), primary_key=True)
    user_id = Column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    token_id = Column(String(36), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    revoked_at = Column(
        DateTime(timezone=True), nullable=True
    )
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    user = relationship("User", back_populates="sessions")


class CollegeSource(Base):
    __tablename__ = "college_sources"
    __table_args__ = (
        UniqueConstraint("source_url", name="uq_college_sources_url"),
        Index("ix_college_sources_status", "status"),
        Index("ix_college_sources_content_hash", "content_hash"),
        CheckConstraint(
            "status IN ('indexed', 'stale', 'failed', 'duplicate')",
            name="ck_college_sources_status",
        ),
        CheckConstraint(
            "source_type IN ('html', 'pdf')",
            name="ck_college_sources_source_type",
        ),
    )

    id = Column(String(64), primary_key=True)
    source_url = Column(String(2048), nullable=False)
    page_title = Column(String(512), nullable=False)
    content_hash = Column(String(64), nullable=False)
    content_text = Column(Text, nullable=False, default="")
    source_type = Column(String(16), nullable=False)
    status = Column(String(16), nullable=False)
    duplicate_of_id = Column(
        String(64),
        ForeignKey("college_sources.id", ondelete="SET NULL"),
        nullable=True,
    )
    last_fetched_at = Column(DateTime(timezone=True), nullable=True)
    last_indexed_at = Column(DateTime(timezone=True), nullable=True)
    last_error = Column(String(64), nullable=True)

    chunks = relationship(
        "CollegeChunk",
        back_populates="source",
        cascade="all, delete-orphan",
        order_by="CollegeChunk.chunk_index",
    )


class CollegeChunk(Base):
    __tablename__ = "college_chunks"
    __table_args__ = (
        UniqueConstraint(
            "source_id",
            "chunk_index",
            name="uq_college_chunks_source_index",
        ),
        Index("ix_college_chunks_source", "source_id"),
    )

    id = Column(String(64), primary_key=True)
    source_id = Column(
        String(64),
        ForeignKey("college_sources.id", ondelete="CASCADE"),
        nullable=False,
    )
    chunk_index = Column(Integer, nullable=False)
    content = Column(Text, nullable=False)

    source = relationship("CollegeSource", back_populates="chunks")
