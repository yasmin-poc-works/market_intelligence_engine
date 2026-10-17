"""JWT share links: signed, scoped to one report, valid for 7 days."""
import logging
import secrets
from datetime import UTC, datetime, timedelta

import jwt

from config import get_settings
from errors import ApiError

log = logging.getLogger("insightforge")

SHARE_TTL = timedelta(days=7)
SCOPE = "report:read"
ALGORITHM = "HS256"


PLACEHOLDERS = {"", "change-me"}
_generated: str | None = None


def jwt_secret() -> str:
    """The signing key from JWT_SECRET. If it is missing or a placeholder, a random key is made once
    per process (with a warning): the API still starts, but share links stop working after a restart."""
    global _generated
    secret = get_settings().jwt_secret
    if secret not in PLACEHOLDERS:
        return secret
    if _generated is None:
        _generated = secrets.token_urlsafe(48)
        log.warning(
            "JWT_SECRET is not set: using a random key for this run, so share links will not survive a restart. "
            'Set JWT_SECRET in .env (python -c "import secrets; print(secrets.token_urlsafe(48))")'
        )
    return _generated


def create_share_token(report_id: str, now: datetime | None = None) -> tuple[str, datetime]:
    now = now or datetime.now(UTC)
    expires = now + SHARE_TTL
    payload = {"sub": report_id, "scope": SCOPE, "iat": now, "exp": expires}
    return jwt.encode(payload, jwt_secret(), algorithm=ALGORITHM), expires


def decode_share_token(token: str) -> str:
    """Returns the report id, or raises a 401 ApiError."""
    try:
        payload = jwt.decode(
            token, jwt_secret(), algorithms=[ALGORITHM], options={"require": ["exp", "sub"]}
        )
    except jwt.ExpiredSignatureError:
        raise ApiError(401, "This share link has expired", "share_expired") from None
    except jwt.PyJWTError:
        raise ApiError(401, "Invalid share link", "share_invalid") from None
    if payload.get("scope") != SCOPE:
        raise ApiError(401, "Invalid share link", "share_invalid")
    return payload["sub"]
