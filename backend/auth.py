import os
import secrets
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pwdlib import PasswordHash
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from database import get_db
from models import AuthSession, User
from schemas import UserCreate, UserLogin, user_response
from security import (
    CSRF_COOKIE,
    _cookie_settings,
    clear_session_cookies,
    csrf_protection,
    get_current_user,
    issue_session,
    make_csrf_token,
)


router = APIRouter(prefix="/auth", tags=["authentication"])
password_hasher = PasswordHash.recommended()
DUMMY_PASSWORD_HASH = password_hasher.hash(secrets.token_urlsafe(32))


def _commit_or_raise(db):
    try:
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise


@router.get("/csrf")
def csrf_token(response: Response):
    token = make_csrf_token()
    secure, same_site = _cookie_settings()
    response.set_cookie(
        CSRF_COOKIE,
        token,
        httponly=False,
        secure=secure,
        samesite=same_site,
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return {"csrf_token": token}


@router.post("/register", status_code=201)
def register(
    data: UserCreate,
    response: Response,
    db: Session = Depends(get_db),
):
    if db.scalar(select(User.id).where(User.email == data.email)):
        raise HTTPException(status_code=409, detail="An account with this email already exists.")

    user = User(
        id=str(uuid.uuid4()),
        name=data.name,
        email=data.email,
        password_hash=password_hasher.hash(data.password),
    )
    db.add(user)
    try:
        db.flush()
        csrf_token = issue_session(response, db, user)
        _commit_or_raise(db)
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="An account with this email already exists.",
        ) from error
    except SQLAlchemyError:
        db.rollback()
        raise
    return {"user": user_response(user), "csrf_token": csrf_token}


@router.post("/login")
def login(
    data: UserLogin,
    response: Response,
    db: Session = Depends(get_db),
):
    user = db.scalar(select(User).where(User.email == data.email))
    stored_hash = user.password_hash if user else DUMMY_PASSWORD_HASH
    if not password_hasher.verify(data.password, stored_hash) or user is None:
        raise HTTPException(status_code=401, detail="Email or password is incorrect.")

    try:
        csrf_token = issue_session(response, db, user)
        _commit_or_raise(db)
    except SQLAlchemyError:
        db.rollback()
        raise
    return {"user": user_response(user), "csrf_token": csrf_token}


@router.get("/me")
def current_user(user: User = Depends(get_current_user)):
    return {"user": user_response(user)}


@router.post("/logout", dependencies=[Depends(csrf_protection)])
def logout(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    try:
        user = get_current_user(request, db)
        session = getattr(request.state, "auth_session", None)
        if session is not None and session.user_id == user.id:
            session.revoked_at = datetime.now(timezone.utc)
            _commit_or_raise(db)
    except HTTPException as error:
        if error.status_code != 401:
            raise
    finally:
        clear_session_cookies(response)
    return {"status": "logged_out"}
