"""
mlb_core.alerts.discord_routes -- the ONE place that decides which Discord webhook a message goes to.

Why this exists: fast_alert_loop used to fall back to DISCORD_WEBHOOK_URL (#daily-picks), so raw +EV
pager alerts buried the daily model card, and the Kambi clone books (sugarhouse + betrivers carry
identical lines) were posted twice. Routing is now a fixed fallback CHAIN per message kind; a kind never
falls back to a channel that belongs to a different audience (an +EV alert never lands in #daily-picks).

    webhook_for("ev_sportsbooks")  DISCORD_WEBHOOK_EV_BOOKS     -> _ALERTS -> _OPS
    webhook_for("ev_exchanges")    DISCORD_WEBHOOK_EV_EXCHANGES -> _ALERTS -> _OPS
    webhook_for("card", sport=..)  DISCORD_WEBHOOK_CARD_<SPORT> -> DISCORD_WEBHOOK_URL
    webhook_for("recap")           DISCORD_WEBHOOK_RECAP        -> (legacy _SUMMARY) -> DISCORD_WEBHOOK_URL
    webhook_for("tweets")          DISCORD_WEBHOOK_TWEETS       -> _OPS
    webhook_for("ops")             DISCORD_WEBHOOK_OPS

Returns None when nothing in the chain is set (callers print instead of posting). Blank / whitespace-only
values count as unset.

Also holds the shared presentation + book helpers for +EV embeds: deep links, the exchange-book set, and the
Kambi clone normalisation.
"""

from __future__ import annotations

import os

SPORTS = ("mlb", "nfl", "nhl", "nba")

_CHAINS = {
    "ev_sportsbooks": ("DISCORD_WEBHOOK_EV_BOOKS", "DISCORD_WEBHOOK_ALERTS", "DISCORD_WEBHOOK_OPS"),
    "ev_exchanges": ("DISCORD_WEBHOOK_EV_EXCHANGES", "DISCORD_WEBHOOK_ALERTS", "DISCORD_WEBHOOK_OPS"),
    # DISCORD_WEBHOOK_SUMMARY is the pre-2026-10-15 name of the recap webhook (secret discord-webhook-summary, still
    # mounted on the service) -- kept in the chain so the live recap does not silently move into #daily-picks.
    "recap": ("DISCORD_WEBHOOK_RECAP", "DISCORD_WEBHOOK_SUMMARY", "DISCORD_WEBHOOK_URL"),
    "tweets": ("DISCORD_WEBHOOK_TWEETS", "DISCORD_WEBHOOK_OPS"),
    "ops": ("DISCORD_WEBHOOK_OPS",),
}
KINDS = tuple(sorted(set(_CHAINS) | {"card"}))


def _first_set(names) -> str | None:
    for name in names:
        val = (os.environ.get(name) or "").strip()
        if val:
            return val
    return None


def webhook_for(kind: str, sport: str | None = None) -> str | None:
    """Webhook URL for a message kind (see module docstring), or None if the whole chain is unset."""
    if kind == "card":
        sp = (sport or "mlb").lower()
        if sp not in SPORTS:
            raise ValueError(f"unknown sport {sport!r}; expected one of {SPORTS}")
        return _first_set((f"DISCORD_WEBHOOK_CARD_{sp.upper()}", "DISCORD_WEBHOOK_URL"))
    chain = _CHAINS.get(kind)
    if chain is None:
        raise ValueError(f"unknown webhook kind {kind!r}; expected one of {KINDS}")
    return _first_set(chain)


# -- books ---------------------------------------------------------------------------------------------------

# Peer-to-peer / zero-fee venues. +EV alerts on these go to #ev-exchanges, everything else to #ev-sportsbooks.
EXCHANGE_BOOKS = frozenset({"novig", "prophetx", "kalshi"})

# Kambi white-label clones quote byte-identical lines, so counting both is a double count (consensus size,
# Discord posts, `bets` rows). Alias -> the book kept.
BOOK_ALIASES = {"sugarhouse": "betrivers"}
EXCLUDE_ALERT_BOOKS = frozenset(BOOK_ALIASES)


def canonical_book(book):
    """Lower-case book key with clone aliases folded in (sugarhouse -> betrivers). Non-strings pass through."""
    if not isinstance(book, str):
        return book
    key = book.strip().lower()
    return BOOK_ALIASES.get(key, key)


def is_exchange_book(book) -> bool:
    return isinstance(book, str) and book.strip().lower() in EXCHANGE_BOOKS


def drop_kambi_clones(df, key_cols, book_col: str = "book"):
    """Drop a clone-book row (sugarhouse) when its twin (betrivers) quotes the same proposition.

    `key_cols` identify the proposition WITHOUT the book (market, game_pk, player_id, line, selection). A clone
    with no twin present is kept: nothing else would alert on that quote. Returns a new frame (same index
    labels, so callers can keep aligning on them)."""
    if df is None or len(df) == 0 or book_col not in df.columns:
        return df
    cols = [c for c in key_cols if c in df.columns]
    book = df[book_col].astype(str).str.strip().str.lower()
    keys = list(zip(*[df[c].where(df[c].notna(), "").astype(str) for c in cols])) if cols else [()] * len(df)
    twins = {k for k, b in zip(keys, book) if b == "betrivers"}
    drop = [b in EXCLUDE_ALERT_BOOKS and k in twins for k, b in zip(keys, book)]
    return df[[not d for d in drop]].copy()


# -- deep links ----------------------------------------------------------------------------------------------

SITE_URL = "https://beezy.fyi"
EV_URL = f"{SITE_URL}/ev"
EDGE_URL = f"{SITE_URL}/edge"
ACTION_LINKS = f"[Open +EV Terminal]({EV_URL}) \N{MIDDLE DOT} [L10 & Multi-Book Cockpit]({EDGE_URL})"


def with_deep_links(embed: dict) -> dict:
    """Make an embed clickable: title links to /ev and the action links close the description. Idempotent."""
    embed["url"] = EV_URL
    desc = (embed.get("description") or "").rstrip()
    if ACTION_LINKS not in desc:
        embed["description"] = f"{desc}\n\n{ACTION_LINKS}" if desc else ACTION_LINKS
    return embed
