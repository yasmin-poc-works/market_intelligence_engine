"""JWT share links: signed, scoped to one report, valid for 7 days."""
from datetime import UTC, datetime, timedelta

import jwt

from config import get_settings
from errors import ApiError

SHARE_TTL = timedelta(days=7)
SCOPE = "report:read"
ALGORITHM = "HS256"


def create_share_token(report_id: str, now: datetime | None = None) -> tuple[str, datetime]:
    now = now or datetime.now(UTC)
    expires = now + SHARE_TTL
    payload = {"sub": report_id, "scope": SCOPE, "iat": now, "exp": expires}
    return jwt.encode(payload, get_settings().jwt_secret, algorithm=ALGORITHM), expires


def decode_share_token(token: str) -> str:
    """Returns the report id, or raises a 401 ApiError."""
    try:
        payload = jwt.decode(
            token, get_settings().jwt_secret, algorithms=[ALGORITHM], options={"require": ["exp", "sub"]}
        )
    except jwt.ExpiredSignatureError:
        raise ApiError(401, "This share link has expired", "share_expired") from None
    except jwt.PyJWTError:
        raise ApiError(401, "Invalid share link", "share_invalid") from None
    if payload.get("scope") != SCOPE:
        raise ApiError(401, "Invalid share link", "share_invalid")
    return payload["sub"]
