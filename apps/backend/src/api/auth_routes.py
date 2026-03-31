"""Authentication routes — token issuance and refresh.

``POST /api/auth/token``
    Issue a new JWT with a fresh session_id.  No authentication required
    (this is the entry point).

``POST /api/auth/refresh``
    Refresh an existing JWT.  Requires a valid (non-expired) Bearer token.
    Returns a new token with the same session_id and a reset expiry.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from src.api.auth import (
    TokenData,
    TokenResponse,
    create_access_token,
    verify_token,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/token", response_model=TokenResponse)
async def issue_token() -> TokenResponse:
    """Issue a new JWT with a fresh session_id.

    Called by the frontend on first load — no auth required.
    """
    token, session_id = create_access_token()
    return TokenResponse(access_token=token, session_id=session_id)


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(
    current: TokenData = Depends(verify_token),
) -> TokenResponse:
    """Refresh an existing JWT, keeping the same session_id.

    Called by the frontend at ~50 minutes to avoid expiry mid-conversation.
    """
    token, session_id = create_access_token(session_id=current.session_id)
    return TokenResponse(access_token=token, session_id=session_id)
