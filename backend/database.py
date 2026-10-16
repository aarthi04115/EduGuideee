import logging
import os
import re
from collections.abc import Generator
from urllib.parse import quote

from dotenv import load_dotenv
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, declarative_base, sessionmaker


logger = logging.getLogger(__name__)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE_DIR, ".env"))
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))


def _normalize_database_url(value: str) -> str:
    value = value.replace("postgres://", "postgresql+psycopg://", 1)
    if value.startswith("postgresql://"):
        value = value.replace("postgresql://", "postgresql+psycopg://", 1)

    match = re.match(
        r"^(?P<prefix>[a-zA-Z][a-zA-Z0-9+.-]*://)"
        r"(?P<authority>[^/?#]+)(?P<suffix>.*)$",
        value,
    )
    if not match or match.group("authority").count("@") <= 1:
        return value

    user_info, host = match.group("authority").rsplit("@", 1)
    if ":" not in user_info:
        raise ValueError("DATABASE_URL has ambiguous unescaped @ characters.")
    username, password = user_info.split(":", 1)
    encoded_user_info = f"{quote(username, safe='')}:{quote(password, safe='')}"
    logger.warning(
        "DATABASE_URL contains an unescaped @ in its password; "
        "the password is URL-encoded in memory. Update the configured URL."
    )
    return f"{match.group('prefix')}{encoded_user_info}@{host}{match.group('suffix')}"


DATABASE_URL = os.getenv("DATABASE_URL")
if DATABASE_URL:
    try:
        DATABASE_URL = _normalize_database_url(DATABASE_URL)
        parsed_database_url = make_url(DATABASE_URL)
    except Exception as error:
        logger.error(
            "DATABASE_URL is malformed (%s); configure a valid database URL.",
            error,
        )
        DATABASE_URL = None
engine = None
if DATABASE_URL:
    try:
        engine = create_engine(
            DATABASE_URL,
            pool_pre_ping=True,
            hide_parameters=True,
        )
    except Exception as error:
        logger.error(
            "Unable to configure the database engine (%s).",
            type(error).__name__,
        )

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)
Base = declarative_base()


def get_db() -> Generator[Session, None, None]:
    if engine is None:
        raise HTTPException(
            status_code=503,
            detail="Database is unavailable. Configure DATABASE_URL and try again.",
        )
    db = SessionLocal()
    try:
        yield db
    except SQLAlchemyError:
        db.rollback()
        raise
    finally:
        db.close()
