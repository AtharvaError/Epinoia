"""Session tokens. This is the only module that knows how a token is made or checked.

To move to Firebase: have verify_token call firebase_admin.auth.verify_id_token and
return the matching user id. Nothing else in the app reads token contents.
"""
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt

from app.config import settings

ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def check_password(password: str, hashed: str | None) -> bool:
    return bool(hashed) and bcrypt.checkpw(password.encode(), hashed.encode())


def issue_token(user_id: int) -> str:
    expires = datetime.now(UTC) + timedelta(hours=settings.session_hours)
    return jwt.encode({"sub": str(user_id), "exp": expires}, settings.jwt_secret, algorithm=ALGORITHM)


def verify_token(token: str) -> int | None:
    """Return the user id for a valid token, otherwise None."""
    try:
        return int(jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM])["sub"])
    except (jwt.InvalidTokenError, KeyError, ValueError):
        return None
