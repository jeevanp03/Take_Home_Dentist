"""JWT authentication helpers.

Provides token creation, verification, and a FastAPI dependency that
extracts the session_id from a valid Bearer token.

Tokens use HS256 (symmetric) via python-jose.  The secret key comes
from ``Settings.JWT_SECRET_KEY``.  Tokens carry a ``session_id`` claim
and expire after 1 hour, with auto-refresh recommended at 50 minutes.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from pydantic import BaseModel

from src.config import get_settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60
REFRESH_WINDOW_MINUTES = 50  # frontend should refresh after this many minutes


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class TokenData(BaseModel):
    """Claims extracted from a validated JWT."""
    session_id: str
    exp: datetime | None = None


class TokenResponse(BaseModel):
    """Response body for token issuance / refresh."""
    access_token: str
    token_type: str = "bearer"
    expires_in: int = ACCESS_TOKEN_EXPIRE_MINUTES * 60  # seconds
    session_id: str


# ---------------------------------------------------------------------------
# Token creation
# ---------------------------------------------------------------------------

def create_access_token(
    session_id: str | None = None,
    expires_delta: timedelta | None = None,
) -> tuple[str, str]:
    """Create a signed JWT with a session_id claim.

    Parameters
    ----------
    session_id:
        If ``None``, a new UUID-based session_id is generated.
    expires_delta:
        Custom expiry.  Defaults to ``ACCESS_TOKEN_EXPIRE_MINUTES``.

    Returns
    -------
    tuple[str, str]:
        ``(token, session_id)``
    """
    if session_id is None:
        session_id = uuid.uuid4().hex[:16]

    settings = get_settings()
    now = datetime.now(timezone.utc)
    expire = now + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))

    claims = {
        "sub": session_id,
        "iat": now,
        "exp": expire,
    }

    token = jwt.encode(claims, settings.JWT_SECRET_KEY, algorithm=ALGORITHM)
    return token, session_id


# ---------------------------------------------------------------------------
# Token verification
# ---------------------------------------------------------------------------

_bearer_scheme = HTTPBearer(auto_error=False)


def _decode_token(token: str) -> TokenData:
    """Decode and validate a JWT, returning the extracted claims.

    Raises ``HTTPException(401)`` on any failure.
    """
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[ALGORITHM])
        session_id: str | None = payload.get("sub")
        if session_id is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token missing session_id claim.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return TokenData(session_id=session_id, exp=payload.get("exp"))
    except JWTError as exc:
        logger.warning("JWT verification failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


async def verify_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> TokenData:
    """FastAPI dependency — extracts and validates the Bearer token.

    Usage::

        @app.get("/protected")
        async def protected(token: TokenData = Depends(verify_token)):
            session_id = token.session_id
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return _decode_token(credentials.credentials)
