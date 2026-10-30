"""Session tokens. This is the only module that knows how a token is made or checked.

To move to Firebase: have verify_token call firebase_admin.auth.verify_id_token and
return the matching user id and version. Nothing else in the app reads token contents.
"""
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt

from app.config import settings

ALGORITHM = "HS256"
# Checked when an account does not exist, so a login takes the same time either way.
_DECOY_HASH = bcrypt.hashpw(b"decoy", bcrypt.gensalt())


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def check_password(password: str, hashed: str | None) -> bool:
    if not hashed:
        bcrypt.checkpw(password.encode(), _DECOY_HASH)
        return False
    return bcrypt.checkpw(password.encode(), hashed.encode())


def issue_token(user_id: int, version: int) -> str:
    expires = datetime.now(UTC) + timedelta(hours=settings.session_hours)
    return jwt.encode({"sub": str(user_id), "v": version, "exp": expires}, settings.jwt_secret, algorithm=ALGORITHM)


def verify_token(token: str) -> tuple[int, int] | None:
    """Return the user id and token version for a valid token, otherwise None."""
    try:
        claims = jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM])
        return int(claims["sub"]), int(claims["v"])
    except (jwt.InvalidTokenError, KeyError, ValueError):
        return None
