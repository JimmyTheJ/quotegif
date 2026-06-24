from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status


def session_secret() -> str:
    import os

    secret = os.environ.get("QUOTEGIF_WEB_SECRET", "").strip()
    if secret:
        return secret
    # Ephemeral secret — sessions reset on restart (dev only).
    return secrets.token_hex(32)


def get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


def require_user(request: Request) -> str:
    username = request.session.get("username")
    if not username:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    return str(username)


CurrentUser = Annotated[str, Depends(require_user)]
