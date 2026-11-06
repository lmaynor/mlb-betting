"""
mlb.runners.waitlist -- pre-launch waitlist signups for the public API.

The one WRITE path in the public API surface: POST /api/public/waitlist
(main.py) stores an email address a visitor typed into the site's own
waitlist form (beezy-vip WaitlistForm, forwarded by the /api/waitlist proxy).
Single opt-in: the form's consent copy is the consent, recorded in the
`consent` column at signup time.

Repeat signups are idempotent: rows are keyed on the normalized (stripped,
lowercased) email, and a resubmission only refreshes source + updated_at.

All queries use text() -- required for pg8000 named params (CONTEXT.md ss8).
The email address is never logged by this module or its route, and
normalize_email's ValueError never echoes the submitted value back.
"""
import re
from datetime import datetime, timezone

from sqlalchemy import text

_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
_MAX_EMAIL_LEN = 254
_MAX_SOURCE_LEN = 64

# Schema follows mlb_core.tracking.bet_tracker: SERIAL on Postgres, with a
# sqlite variant (AUTOINCREMENT) swapped in for tests.
_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS waitlist_signups (
    id         SERIAL PRIMARY KEY,
    email      TEXT NOT NULL UNIQUE,
    source     TEXT,
    consent    BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""

_SCHEMA_SQL_SQLITE = _SCHEMA_SQL.replace(
    "id         SERIAL PRIMARY KEY",
    "id         INTEGER PRIMARY KEY AUTOINCREMENT",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_email(email) -> str:
    """Strip + lowercase a submitted address, or raise ValueError.

    The error message is deliberately generic: routes surface it as the 400
    body, and it must never contain the submitted address.
    """
    if not isinstance(email, str):
        raise ValueError("invalid email address")
    e = email.strip().lower()
    if len(e) > _MAX_EMAIL_LEN or not _EMAIL_RE.match(e):
        raise ValueError("invalid email address")
    return e


def _clean_source(source) -> str:
    """Which site form produced the signup ('pricing', 'signup', 'footer', ...).
    Anything unexpected collapses to 'site'; never stored raw."""
    if not isinstance(source, str):
        return "site"
    s = re.sub(r"[^a-z0-9_-]", "", source.strip().lower())[:_MAX_SOURCE_LEN]
    return s or "site"


def ensure_waitlist_table(engine) -> None:
    """CREATE TABLE IF NOT EXISTS, dialect-flavoured like BetTracker's schema.
    Called from the signup path itself: the API can be the first process to
    run after a deploy (same pattern as _ensure_proj_col in public_api)."""
    ddl = _SCHEMA_SQL_SQLITE if engine.dialect.name == "sqlite" else _SCHEMA_SQL
    with engine.begin() as conn:
        conn.execute(text(ddl))


def add_waitlist_signup(engine, email, source=None) -> dict:
    """Insert a waitlist signup, or refresh source/updated_at if the email is
    already on the list. Returns {"status": "created" | "existing"}.
    Raises ValueError on a malformed email."""
    e = normalize_email(email)
    src = _clean_source(source)
    now = _now_iso()
    ensure_waitlist_table(engine)
    with engine.begin() as conn:
        existing = conn.execute(
            text("SELECT id FROM waitlist_signups WHERE email = :email"),
            {"email": e},
        ).first()
        conn.execute(
            text(
                "INSERT INTO waitlist_signups (email, source, consent, created_at, updated_at) "
                "VALUES (:email, :source, :consent, :now, :now) "
                "ON CONFLICT (email) DO UPDATE SET "
                "source = excluded.source, updated_at = excluded.updated_at"
            ),
            {"email": e, "source": src, "consent": True, "now": now},
        )
    return {"status": "existing" if existing else "created"}
