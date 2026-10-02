import hmac

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.security import COOKIE_NAME, csrf_token, token_hash
from app.db.session import get_db
from app.models import AuthSession, User
from app.models.foundation import utc_now


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get(COOKIE_NAME, "")
    if not token or len(token) > 256:
        raise HTTPException(401, "Login required")
    session = db.get(AuthSession, token_hash(token))
    if session is None or session.expires_at <= utc_now():
        raise HTTPException(401, "Session expired")
    user = db.get(User, session.user_id)
    if user is None or not user.enabled:
        raise HTTPException(401, "Account unavailable")
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        expected = csrf_token(token, request.app.state.settings.session_secret.get_secret_value())
        if not hmac.compare_digest(request.headers.get("X-CSRF-Token", ""), expected):
            raise HTTPException(403, "Invalid CSRF token")
    return user


def admin_user(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(403, "Administrator permission required")
    return user


def operator_user(user: User = Depends(current_user)) -> User:
    if user.role not in {"admin", "operator"}:
        raise HTTPException(403, "Operator permission required")
    return user
