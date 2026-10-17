"""Authentication runs before any request body is consumed."""

import hashlib
import hmac

from fastapi import HTTPException, Request, Security
from fastapi.security import APIKeyHeader

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def key_digests(value: str) -> list[tuple[str, bytes]]:
    result = []
    for entry in value.split(","):
        if not entry.strip():
            continue
        name, separator, secret = entry.strip().partition(":")
        if not separator or not secret or name not in {"jury", "team"}:
            raise ValueError("CG_API_KEYS must contain jury:<token>,team:<token>")
        result.append((name, hashlib.sha256(secret.encode()).digest()))
    return result


async def require_api_key(request: Request, key: str | None = Security(api_key_header)) -> str:
    if not key:
        raise HTTPException(401, "invalid or missing API key")
    digest = hashlib.sha256(key.encode()).digest()
    principal = None
    for name, reference in request.app.state.key_digests:
        if hmac.compare_digest(digest, reference):
            principal = name
    if principal is None:
        raise HTTPException(401, "invalid or missing API key")
    return principal
