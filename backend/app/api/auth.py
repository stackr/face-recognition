import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.api.dependencies import current_user
from app.core.security import (
    COOKIE_NAME,
    DUMMY_PASSWORD_HASH,
    csrf_token,
    password_hasher,
    token_hash,
)
from app.db.session import get_db
from app.models import AuditLog, AuthSession, User
from app.models.foundation import utc_now
from app.schemas.foundation import AuthOutput, LoginInput, UserOutput

router = APIRouter(prefix="/api/auth", tags=["Authentication"])


@router.post("/login", response_model=AuthOutput)
def login(payload: LoginInput, request: Request, response: Response, db: Session = Depends(get_db)):
    key = request.client.host if request.client else "unknown"
    if not request.app.state.login_limiter.allow(key):
        raise HTTPException(429, "Too many login attempts; try again later")
    user = db.scalar(select(User).where(User.username == payload.username))
    hashed = user.password_hash if user is not None else DUMMY_PASSWORD_HASH
    valid = password_hasher.verify(payload.password.get_secret_value(), hashed)
    if not valid or user is None or not user.enabled:
        db.add(AuditLog(action="login.failed", resource_type="user"))
        db.commit()
        raise HTTPException(401, "Invalid username or password")
    settings = request.app.state.settings
    token = secrets.token_urlsafe(48)
    db.execute(delete(AuthSession).where(AuthSession.expires_at <= utc_now()))
    db.add(
        AuthSession(
            token_hash=token_hash(token),
            user_id=user.id,
            expires_at=utc_now() + timedelta(seconds=settings.session_ttl_seconds),
        )
    )
    db.add(
        AuditLog(
            user_id=user.id, action="login.success", resource_type="user", resource_id=str(user.id)
        )
    )
    db.commit()
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        max_age=settings.session_ttl_seconds,
        path="/",
    )
    return AuthOutput(
        user=UserOutput.model_validate(user),
        csrf_token=csrf_token(token, settings.session_secret.get_secret_value()),
    )


@router.get("/me", response_model=AuthOutput)
def me(request: Request, user: User = Depends(current_user)):
    return AuthOutput(
        user=UserOutput.model_validate(user),
        csrf_token=csrf_token(
            request.cookies[COOKIE_NAME],
            request.app.state.settings.session_secret.get_secret_value(),
        ),
    )


@router.post("/logout", status_code=204)
def logout(
    request: Request,
    response: Response,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    db.execute(
        delete(AuthSession).where(
            AuthSession.token_hash == token_hash(request.cookies[COOKIE_NAME])
        )
    )
    db.add(
        AuditLog(user_id=user.id, action="logout", resource_type="user", resource_id=str(user.id))
    )
    db.commit()
    response.delete_cookie(
        COOKIE_NAME,
        path="/",
        secure=request.app.state.settings.cookie_secure,
        httponly=True,
        samesite="strict",
    )
