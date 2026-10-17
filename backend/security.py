import hashlib
import hmac
import logging
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, Request, Response
from fastapi import Header
from jwt import InvalidTokenError
from sqlalchemy import select
from sqlalchemy.orm import Session

from database import get_db
from models import AuthSession, User


logger = logging.getLogger(__name__)
SESSION_COOKIE = "eduguide_session"
CSRF_COOKIE = "eduguide_csrf"
TOKEN_ISSUER = "eduguide"
TOKEN_AUDIENCE = "eduguide-web"
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
if JWT_ALGORITHM != "HS256":
    logger.error("JWT_ALGORITHM must be HS256; refusing unsafe algorithm configuration.")
JWT_ALGORITHM = "HS256"


def _jwt_secret():
    secret = os.getenv("JWT_SECRET_KEY", "")
    if len(secret.encode("utf-8")) < 32:
        raise HTTPException(
            status_code=503,
            detail="Authentication is not configured. Set a random JWT_SECRET_KEY of at least 32 bytes.",
        )
    return secret


def _token_lifetime():
    try:
        minutes = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))
    except ValueError as error:
        raise HTTPException(
            status_code=503,
            detail="Authentication token lifetime is not configured correctly.",
        ) from error
    if minutes < 5 or minutes > 1440:
        raise HTTPException(
            status_code=503,
            detail="Authentication token lifetime must be between 5 and 1440 minutes.",
        )
    return timedelta(minutes=minutes)


def _cookie_settings():
    is_render = bool(os.getenv("RENDER") or os.getenv("RENDER_SERVICE_ID"))
    raw_samesite = os.getenv("COOKIE_SAMESITE", "").strip().lower()
    if not raw_samesite:
        same_site = "none" if is_render else "lax"
    else:
        same_site = raw_samesite

    if same_site not in {"lax", "strict", "none"}:
        raise HTTPException(
            status_code=503,
            detail="COOKIE_SAMESITE must be 'lax', 'strict', or 'none'.",
        )
    raw_secure = os.getenv("COOKIE_SECURE", "").strip().lower()
    if raw_secure:
        secure = raw_secure == "true"
    else:
        secure = True if (is_render or same_site == "none") else False

    if same_site == "none":
        secure = True
    return secure, same_site


def make_csrf_token():
    nonce = secrets.token_urlsafe(32)
    signature = hmac.new(
        _jwt_secret().encode("utf-8"),
        nonce.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{nonce}.{signature}"


def verify_csrf(request: Request, csrf_header: str | None):
    csrf_cookie = request.cookies.get(CSRF_COOKIE)
    if not csrf_cookie or not csrf_header or not hmac.compare_digest(
        csrf_cookie, csrf_header
    ):
        raise HTTPException(status_code=403, detail="CSRF validation failed.")
    try:
        nonce, supplied_signature = csrf_cookie.rsplit(".", 1)
    except ValueError as error:
        raise HTTPException(status_code=403, detail="CSRF validation failed.") from error
    expected_signature = hmac.new(
        _jwt_secret().encode("utf-8"),
        nonce.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(supplied_signature, expected_signature):
        raise HTTPException(status_code=403, detail="CSRF validation failed.")


def csrf_protection(
    request: Request,
    x_csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
):
    verify_csrf(request, x_csrf_token)


def issue_session(response: Response, db: Session, user: User):
    now = datetime.now(timezone.utc)
    expires_at = now + _token_lifetime()
    token_id = str(uuid.uuid4())
    token = jwt.encode(
        {
            "sub": user.id,
            "jti": token_id,
            "iat": now,
            "exp": expires_at,
            "iss": TOKEN_ISSUER,
            "aud": TOKEN_AUDIENCE,
        },
        _jwt_secret(),
        algorithm=JWT_ALGORITHM,
    )
    session = AuthSession(
        id=str(uuid.uuid4()),
        user_id=user.id,
        token_id=token_id,
        expires_at=expires_at,
    )
    db.add(session)
    secure, same_site = _cookie_settings()
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int((expires_at - now).total_seconds()),
        httponly=True,
        secure=secure,
        samesite=same_site,
        path="/",
    )
    csrf_token = make_csrf_token()
    response.set_cookie(
        CSRF_COOKIE,
        csrf_token,
        max_age=int((expires_at - now).total_seconds()),
        httponly=False,
        secure=secure,
        samesite=same_site,
        path="/",
    )
    return csrf_token


def clear_session_cookies(response: Response):
    secure, same_site = _cookie_settings()
    response.delete_cookie(
        SESSION_COOKIE,
        path="/",
        secure=secure,
        httponly=True,
        samesite=same_site,
    )
    response.delete_cookie(
        CSRF_COOKIE,
        path="/",
        secure=secure,
        httponly=False,
        samesite=same_site,
    )


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
) -> User:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required.")
    try:
        claims = jwt.decode(
            token,
            _jwt_secret(),
            algorithms=[JWT_ALGORITHM],
            issuer=TOKEN_ISSUER,
            audience=TOKEN_AUDIENCE,
            options={"require": ["exp", "iat", "sub", "jti", "iss", "aud"]},
        )
        token_id = str(uuid.UUID(claims["jti"]))
        user_id = str(uuid.UUID(claims["sub"]))
    except (InvalidTokenError, KeyError, TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Session is invalid or expired.")

    now = datetime.now(timezone.utc)
    session = db.scalar(
        select(AuthSession).where(
            AuthSession.token_id == token_id,
            AuthSession.user_id == user_id,
            AuthSession.revoked_at.is_(None),
            AuthSession.expires_at > now,
        )
    )
    user = db.get(User, user_id) if session else None
    if user is None:
        raise HTTPException(status_code=401, detail="Session is invalid or expired.")
    request.state.auth_session = session
    return user
