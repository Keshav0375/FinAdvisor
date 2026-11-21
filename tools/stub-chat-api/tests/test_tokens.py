import base64
import hashlib
import json

import pytest

from tokens import TokenError, build_unsigned_jwt, decode_unverified, digest_header


def _jwt(payload: object) -> str:
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"e30.{body}."


def test_digest_header_hashes_full_value_and_counts_bytes():
    value = "Bearer SENTINEL_AAA"
    assert digest_header(value) == {
        "sha256": hashlib.sha256(value.encode()).hexdigest(),
        "length": len(value),
    }


def test_build_unsigned_jwt_has_alg_none_and_empty_signature():
    token = build_unsigned_jwt("https://x", "a@b", now=1000, ttl_s=60)
    header, payload, signature = token.split(".")
    assert signature == ""
    assert json.loads(base64.urlsafe_b64decode(header + "==")) == {"alg": "none", "typ": "JWT"}
    claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    assert claims["aud"] == "https://x"
    assert claims["email"] == "a@b"
    assert claims["iat"] == 1000
    assert claims["exp"] == 1060


def test_decode_round_trip_with_and_without_bearer_prefix():
    token = build_unsigned_jwt("https://x", "a@b", now=1000, ttl_s=60)
    expected = {"aud": "https://x", "email": "a@b", "exp": 1060}
    assert decode_unverified(f"Bearer {token}") == expected
    assert decode_unverified(f"bearer   {token}") == expected
    assert decode_unverified(token) == expected


def test_decode_missing_claims_are_none():
    assert decode_unverified(_jwt({})) == {"aud": None, "email": None, "exp": None}


@pytest.mark.parametrize(
    "value",
    [
        "",
        "Bearer",
        "Bearer a.b",
        "Bearer a.b.c.d",
        "Bearer e30.***.",
        "Bearer e30.é.",
        "x.bm90anNvbg.",
    ],
)
def test_decode_rejects_malformed(value):
    with pytest.raises(TokenError):
        decode_unverified(value)


def test_decode_rejects_non_object_payload():
    with pytest.raises(TokenError, match="not a JSON object"):
        decode_unverified(_jwt([1, 2]))


def test_decode_rejects_non_utf8_payload():
    bad = base64.urlsafe_b64encode(b"\xff\xfe").rstrip(b"=").decode()
    with pytest.raises(TokenError):
        decode_unverified(f"e30.{bad}.")
