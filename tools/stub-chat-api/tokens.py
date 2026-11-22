"""Pure helpers: header digests and unsigned JWTs. No I/O, no clock (callers pass `now`)."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from typing import Any

BEARER_PREFIX = "bearer "
ISSUER = "https://accounts.google.com"


class TokenError(ValueError):
    """The header value is not a decodable JWT."""


def digest_header(value: str) -> dict[str, Any]:
    """Return {sha256, length} of the full header value, never the value itself."""
    raw = value.encode("utf-8")
    return {"sha256": hashlib.sha256(raw).hexdigest(), "length": len(raw)}


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(segment: str) -> bytes:
    padded = segment + "=" * (-len(segment) % 4)
    try:
        return base64.urlsafe_b64decode(padded.encode("ascii"))
    except (binascii.Error, UnicodeEncodeError) as exc:
        raise TokenError("segment is not base64url") from exc


def build_unsigned_jwt(audience: str, email: str, now: int, ttl_s: int) -> str:
    """Return an `alg: none` JWT shaped like a GCE metadata identity token.

    The `stub: true` claim marks it so it is never mistaken for a real Google token.
    """
    header = {"alg": "none", "typ": "JWT"}
    claims = {
        "aud": audience,
        "azp": email,
        "email": email,
        "email_verified": True,
        "iss": ISSUER,
        "stub": True,
        "iat": now,
        "exp": now + ttl_s,
    }
    parts = [
        _b64url_encode(json.dumps(part, separators=(",", ":")).encode())
        for part in (header, claims)
    ]
    return ".".join([*parts, ""])


def decode_unverified(header_value: str) -> dict[str, Any]:
    """Return {aud, email, exp} from the payload of `Bearer <jwt>`; the signature is not checked."""
    token = header_value.strip()
    if token.lower().startswith(BEARER_PREFIX):
        token = token[len(BEARER_PREFIX) :].strip()
    parts = token.split(".")
    if len(parts) != 3:
        raise TokenError("expected three dot-separated segments")
    try:
        claims = json.loads(_b64url_decode(parts[1]))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TokenError("payload is not JSON") from exc
    if not isinstance(claims, dict):
        raise TokenError("payload is not a JSON object")
    return {"aud": claims.get("aud"), "email": claims.get("email"), "exp": claims.get("exp")}
