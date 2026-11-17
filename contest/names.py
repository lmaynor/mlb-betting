"""Display names: what other players see on the leaderboard and in the Pool.

A contest is a room of friends, so the rules aim at the two real problems: two people who look identical (impersonation) and
names that say nothing (a raw email address). They are deliberately simple; an admin can rename anyone (`store.admin_rename`).

* 2 to 20 characters after cleaning; letters (any alphabet), digits, spaces and . _ ' -
* spaces collapsed, ends trimmed; at least two letters or digits; no URLs
* unique per contest, compared by a KEY: case-folded with every non-alphanumeric dropped, so "Alex", "ALEX", "a.l.e.x" and "Alex_"
  are one name. Lookalikes across alphabets (a Cyrillic "a") are NOT caught: admin rename is the backstop.
* a short reserved list that would read as staff or the house ("admin", "house", "you" ...)
* at most `MAX_RENAMES` changes per entry, so a name cannot be swapped to dodge a bad bet
"""
from __future__ import annotations

import re
import unicodedata

MIN_LEN, MAX_LEN, MAX_RENAMES = 2, 20, 3
RESERVED = frozenset({"you", "me", "admin", "administrator", "house", "dealer", "beezy", "beezyfyi", "mod", "moderator", "support",
                      "staff", "system", "owner", "contest", "bot", "null", "undefined"})
_ALLOWED = re.compile(r"^[\w .'\-]+$", re.UNICODE)


class NameInvalid(Exception):
    """`code` is NAME_INVALID or NAME_RESERVED; `message` is safe to show the player."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code, self.message = code, message


def clean(raw: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(raw or "")).split())


def name_key(display: str) -> str:
    """The uniqueness key: case-folded, letters and digits only."""
    return "".join(ch for ch in unicodedata.normalize("NFKC", display).casefold() if ch.isalnum())


def validate_display_name(raw: str) -> tuple[str, str]:
    """-> (display name, key). Raises NameInvalid with a message fit to show."""
    name = clean(raw)
    if not MIN_LEN <= len(name) <= MAX_LEN:
        raise NameInvalid("NAME_INVALID", f"Use {MIN_LEN} to {MAX_LEN} characters.")
    if not _ALLOWED.match(name):
        raise NameInvalid("NAME_INVALID", "Use letters, numbers, spaces and . _ ' - only.")
    if name[0] in ".'-_" or name[-1] in ".'-_":
        raise NameInvalid("NAME_INVALID", "Start and end with a letter or number.")
    key = name_key(name)
    if len(key) < 2:
        raise NameInvalid("NAME_INVALID", "Include at least two letters or numbers.")
    if "http" in key or "www" in key or ("." in name and key.endswith(("com", "net", "org", "io"))):
        raise NameInvalid("NAME_INVALID", "Links are not allowed in a name.")
    if key in RESERVED:
        raise NameInvalid("NAME_RESERVED", "That name is reserved.")
    return name, key


def suggest_display_name(first: str | None = None, last: str | None = None, username: str | None = None, email: str | None = None) -> str:
    """A starting point for the join form from what the account knows: the username, else "First L", else the part of the email before
    the @ tidied into words. The player always sees it and can edit it. Falls back to "Player"."""
    first_last = " ".join(p for p in (first, (last or "")[:1]) if p)
    from_email = re.sub(r"[._\-]+", " ", (email or "").split("@")[0]).title()
    for cand in (username, first_last, from_email):
        try:
            return validate_display_name(cand or "")[0]
        except NameInvalid:
            continue
    return "Player"
