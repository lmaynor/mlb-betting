---
title: Main tweets are link-free; the link is a thread reply; reply snippets carry no link or brand
module: tweet_drafter.py
tags: [tweet-drafter, discord, x, reply-ammo, link-policy]
problem_type: convention
category: conventions
date: 2026-10-19
---

## Convention

- **A main tweet never carries a URL.** X deprioritizes posts from small accounts that contain an outbound link, so the draft ends
  with the plain-text watermark `beezy.fyi` (no scheme, no path). Gemini is told so in `SYSTEM_PROMPT` and both prompts, and
  `tweet_drafter.strip_urls()` enforces it on the output (any beezy.fyi URL becomes the watermark, any other URL is dropped).
- **The link is a reply the operator posts under their own tweet.** Typefully was removed 2026-10-19 (nothing posts to X
  automatically): `#tweet-queue` is the only output, and each draft's Discord message carries the reply text
  (`build_link_reply(label, url)`). picks -> `/picks`, recap/settlement -> `/results`. An X intent link cannot make a thread, so the
  reply is a manual second step.
- **Every drafting mode posts to Discord `#tweet-queue` as PLAIN messages** (`webhook_for("tweets")`): one short header line, then
  each tweet variant as its own message whose entire content is the tweet text (Discord's "Copy Text" gives exactly what goes on X --
  no code fences, labels or links), then the OG card image, then the link reply (embed unfurl suppressed). `post_messages()` paces
  the posts and retries one 429. The 1-tap `x.com/intent` links were REMOVED 2026-10-20: on the operator's phone one opened the
  composer with the text still percent-encoded (`%20`, `%0A`), and the code blocks/labels made copying painful. Because Discord is the
  only sink, `require_posted()` raises on a failed or skipped post so the Job fails visibly.
- **`reply_ammo` snippets are for replying under other accounts' posts**: a plain market fact (book price vs the OTHER books'
  de-vigged fair), <= 180 chars, no URL, no `beezy`, no `@`. `is_link_free()` is the invariant and the builder returns None (skips the
  play) rather than truncating. The intent link is deliberately NOT offered for replies: it cannot target a tweet id, so a tap would
  post the snippet as a context-free standalone tweet.

## Units are flat 1u per bet: result = profit / stake (2026-10-20)

`bets.profit` and `bets.stake` are DOLLARS: EV alert rows stake a flat $100, model rows a Kelly dollar amount (`kelly_stake` returns
dollars). The site's unit is "a flat 1 unit at the flagged price", so a bet's result in units is `profit / stake` (`bet_units()`),
and a day's result is the sum. The first version of tweet_drafter printed `profit` straight into a `u` format and posted a
-$190 day as "-190u" (true: -1.90u across 6 bets). Never put `bets.profit` next to a `u`; the OG settlement card had the same bug and
now uses `unitsOf()`. `mlb_core/risk/exposure.py` also defines a bankroll unit (`UNIT_PCT = 1%`) used for Kelly caps; that is not
what the site or the tweets report.

Also found by running the prompts on live data: (1) EV alerts log the same proposition once per book, so the recap prompt collapses
identical legs ("x2 tickets"); (2) for `system="EV"` rows `model_prob` is the OTHER books' consensus, not our model, so the picks
prompt says so; (3) the recap fetched 10 rows, which can cut a busy slate, it now fetches 200.

## Voice: plain, specific, no stock phrases (2026-10-19, reworked 2026-10-20)

`PERSONA` (shared by `SYSTEM_PROMPT` and `REPLY_SYSTEM_PROMPT`) is a sharp, dry friend: concrete first, only the numbers given,
ordinary betting talk, results flat. The first version leaned on example phrases ("took it on the chin", "fall asleep on this line")
and the model parroted them in every draft, and the recap prompt itself said "the transparency is the brand ... cite the season sample
size", which produced defensive boasting. Those phrases are now in `BANNED_PHRASES` and out of the prompts; do not put catchphrases
in a prompt as examples. `de-vigged`, `consensus fair` and `proj_mean` stay banned.

`reply_ammo` asks Gemini ONCE (batched) for `slow_book` / `model_first` / `deadpan` replies per play, and a reply is used only if
`reply_variant_ok()` passes: link-free, in voice, <= 180 chars, quotes the book's real price, and contains no number that was not
given for that play. A conversational model will invent a price if you let it. A play with no usable reply (or Gemini down) falls
back to the deterministic `build_reply_snippet()`.

## Gemini reliability

`_gemini_json_text()` asks for JSON mode (`responseMimeType`, plus a string-array schema for the tweet drafts) with a 4096-token
budget, and `generate_tweets()` re-asks up to 3 times on invalid JSON or an all-rejected reply. 2026-10-19: picks died on
`Expecting value` and a recap attempt on `Unterminated string`, both a reply truncated by thinking tokens under the old 1536 budget.

## Not verified

The 50-80% reach penalty for outbound links is the premise of this convention, taken from outside advice, not measured on this
account. A bare `beezy.fyi` may still be auto-linked by X. Check how the first real
tweet-then-reply looks before relying on it.

## SETTLEMENT_SPORTS excludes NFL on purpose

`bets.sport` is `mlb|nhl|nba` (`bet_tracker.VALID_SPORTS`); NFL is shadow-only, so `/api/public/picks/recent?sport=nfl` answers 400 and
there is nothing to settle. Add "nfl" to `tweet_drafter.SETTLEMENT_SPORTS` and the settlement-card route's `SPORTS` only once NFL
bets are tracked (`tests/test_tweet_drafter.py::test_nfl_is_not_a_settlement_sport_because_nfl_bets_are_not_tracked` pins this).
