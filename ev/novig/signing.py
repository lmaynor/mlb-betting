"""
ev.novig.signing -- NOVIG-V3 request signing (https://docs.novig.com/api/signing).

Canonical string = six lines joined by LF, no trailing newline:
  NOVIG-V3 / {unix_millis} / {METHOD} / {path, verbatim} / {canonical_query} / {sha256(raw body) lowercase hex}
Headers on EVERY request (GET included): Novig-Key-Id, Novig-Timestamp, Novig-Signature (padded base64).
Ed25519 only here (the docs' P-256 needs DER-encoded ECDSA; not needed). Verified against the 30 official vectors
(tests/fixtures/ev/novig_signing_vectors.json).
"""

from __future__ import annotations

import base64
import hashlib
from urllib.parse import unquote_to_bytes

_UNRESERVED = frozenset(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")


def _encode(part: bytes) -> str:
    return "".join(chr(b) if b in _UNRESERVED else f"%{b:02X}" for b in part)


def canonical_query(raw: str) -> str:
    """Raw query -> sorted, decoded-then-reencoded pairs joined by '&' ('' when there is no query)."""
    pairs = []
    for pair in (raw or "").split("&"):
        if not pair:
            continue
        name, _, value = pair.partition("=")
        pairs.append((_encode(unquote_to_bytes(name)), _encode(unquote_to_bytes(value))))   # '+' stays literal
    pairs.sort()
    return "&".join(f"{k}={v}" for k, v in pairs)


def body_hash(body: bytes) -> str:
    return hashlib.sha256(body or b"").hexdigest()


def string_to_sign(ts_ms: int | str, method: str, path: str, raw_query: str, body: bytes) -> str:
    return "\n".join(["NOVIG-V3", str(ts_ms), method.upper(), path, canonical_query(raw_query), body_hash(body)])


def load_ed25519(pem: bytes):
    from cryptography.hazmat.primitives.serialization import load_pem_private_key
    key = load_pem_private_key(pem, password=None)
    return key


def sign(private_key, canonical: str) -> str:
    return base64.b64encode(private_key.sign(canonical.encode())).decode()


def auth_headers(key_id: str, private_key, ts_ms: int, method: str, path: str, raw_query: str, body: bytes) -> dict:
    return {"Novig-Key-Id": key_id, "Novig-Timestamp": str(ts_ms),
            "Novig-Signature": sign(private_key, string_to_sign(ts_ms, method, path, raw_query, body))}
