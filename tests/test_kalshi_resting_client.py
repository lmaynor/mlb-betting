"""Resting-order prerequisites for mlb.execution.kalshi (PR-1, Argon Part A).

Mocked transport only: a FakeSession records every request the real
KalshiClient makes (signing included, against a throwaway RSA key) and returns
canned responses. No network, no live exchange calls.

Covered:
  * cancel_order sends market_ticker (never `ticker`) -- the V2 auto-routing rule
  * create_order post_only payload + KalshiVenue.submit_resting (GTC/post-only)
    vs. the unchanged IOC taker submit (regression)
  * queue-position reads (single + per-market), fixed-point strings -> float
  * amend semantics: size-down / expiry are queue-preserving, price change /
    size-up are queue-forfeiting, encoded as separate validated methods
  * client_order_id determinism for resting orders (reconciliation key)
"""
import inspect

import pytest

from mlb.execution.kalshi.client import KalshiClient
from mlb.execution.kalshi.venue import KalshiVenue, OrderIntent, client_order_id

GRID = ()  # no price ranges: snapping rounds to 4dp, prices below are on-grid


# --------------------------------------------------------------------------- #
# Fake transport (same style as test_kalshi_exec_pipeline's _Resp/_Sess)
# --------------------------------------------------------------------------- #

class _Resp:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._b = body or {}
        self.headers = {}
        self.content = b"x"
        self.text = str(self._b)

    def json(self):
        return self._b


class _Sess:
    """requests.Session stand-in: records (method, url, params, json) per call."""

    def __init__(self, seq):
        self.seq = list(seq)
        self.headers = {}
        self.calls = []

    def request(self, method, url, params=None, json=None, headers=None, timeout=None):
        self.calls.append({"method": method, "url": url, "params": params,
                           "json": json, "headers": headers})
        return self.seq.pop(0)


@pytest.fixture(scope="module")
def pem():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(serialization.Encoding.PEM,
                             serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption())


def _client(pem, seq):
    sess = _Sess(seq)
    c = KalshiClient("demo", "KEY-ID", pem, session=sess)
    c.min_interval_s = 0.0
    return c, sess


# --------------------------------------------------------------------------- #
# 1. cancel_order: market_ticker, not ticker
# --------------------------------------------------------------------------- #

def test_cancel_sends_market_ticker_and_never_ticker(pem):
    c, sess = _client(pem, [_Resp(200, {"order_id": "o1", "reduced_by": "1.00"})])
    out = c.cancel_order("o1", ticker="KXMVE-T")
    assert out == {"order_id": "o1", "reduced_by": "1.00"}
    (call,) = sess.calls
    assert call["method"] == "DELETE"
    assert call["url"].endswith("/portfolio/events/orders/o1")
    # Exact params dict: market_ticker only -- the old `ticker` param 404'd on demo.
    assert call["params"] == {"market_ticker": "KXMVE-T"}
    assert "ticker" not in call["params"]


def test_cancel_without_ticker_sends_no_params(pem):
    c, sess = _client(pem, [_Resp(200, {})])
    c.cancel_order("o1")
    assert sess.calls[0]["params"] is None


# --------------------------------------------------------------------------- #
# 2. create_order post_only + resting venue path (taker path unchanged)
# --------------------------------------------------------------------------- #

def test_create_order_post_only_only_sent_when_true(pem):
    c, sess = _client(pem, [_Resp(200, {"order_id": "o1"}), _Resp(200, {"order_id": "o2"})])
    c.create_order(ticker="T", client_order_id="c1", book_side="bid", count=2, price=0.01,
                   time_in_force="good_till_canceled", post_only=True)
    body = sess.calls[0]["json"]
    assert body["post_only"] is True
    assert body["time_in_force"] == "good_till_canceled"
    assert body["count"] == "2.00" and body["price"] == "0.0100"
    # Default (taker) call: no post_only key at all, so the taker payload is unchanged.
    c.create_order(ticker="T", client_order_id="c2", book_side="bid", count=1, price=0.5)
    body2 = sess.calls[1]["json"]
    assert "post_only" not in body2
    assert body2["time_in_force"] == "immediate_or_cancel"


class FakeVenueClient:
    """Venue-level fake, same style as test_kalshi_exec_pipeline.FakeClient."""

    def __init__(self, fills=None, raise_exc=None):
        self.calls = []
        self._fills = fills or []
        self._exc = raise_exc

    def create_order(self, **kw):
        self.calls.append(kw)
        if self._exc:
            raise self._exc
        return {"order_id": "ord-1", "client_order_id": kw["client_order_id"],
                "fill_count": "0.00", "remaining_count": f"{kw['count']:.2f}"}

    def get_fills(self, **kw):
        return self._fills


def test_submit_resting_places_gtc_post_only_order():
    fc = FakeVenueClient()
    ack = KalshiVenue(fc).submit_resting(
        OrderIntent("intent-r1", "KXMVE-T", "no", 2, 0.99, GRID, exchange_index=1))
    (call,) = fc.calls
    assert call["time_in_force"] == "good_till_canceled"
    assert call["post_only"] is True
    assert call["ticker"] == "KXMVE-T" and call["count"] == 2.0
    assert call["exchange_index"] == 1
    assert ack.status == "RESTING" and ack.order_id == "ord-1" and ack.fill_count == 0.0


def test_taker_submit_still_sends_ioc_without_post_only():
    fc = FakeVenueClient()
    ack = KalshiVenue(fc).submit(OrderIntent("intent-t1", "T", "yes", 5, 0.5, GRID))
    (call,) = fc.calls
    assert call["time_in_force"] == "immediate_or_cancel"
    assert "post_only" not in call  # venue taker path does not pass it at all
    assert ack.status == "NO_FILL"


def test_submit_resting_error_is_captured_not_raised():
    fc = FakeVenueClient(raise_exc=RuntimeError("boom"))
    ack = KalshiVenue(fc).submit_resting(OrderIntent("i", "T", "yes", 1, 0.5, GRID))
    assert ack.status == "ERROR" and "boom" in ack.error and ack.order_id is None


def test_resting_client_order_id_is_deterministic():
    intent = OrderIntent("intent-r1", "KXMVE-T", "no", 2, 0.99, GRID)
    acks = [KalshiVenue(FakeVenueClient()).submit_resting(intent) for _ in range(2)]
    assert acks[0].client_order_id == acks[1].client_order_id
    assert acks[0].client_order_id == client_order_id("intent-r1")
    other = KalshiVenue(FakeVenueClient()).submit_resting(
        OrderIntent("intent-r2", "KXMVE-T", "no", 2, 0.99, GRID))
    assert other.client_order_id != acks[0].client_order_id


# --------------------------------------------------------------------------- #
# 3. Queue-position reads
# --------------------------------------------------------------------------- #

def test_get_order_queue_position_parses_fixed_point(pem):
    c, sess = _client(pem, [_Resp(200, {"queue_position_fp": "0.00"}),
                            _Resp(200, {"queue_position_fp": "12.50"})])
    assert c.get_order_queue_position("o1") == 0.0
    assert c.get_order_queue_position("o2") == 12.5
    assert sess.calls[0]["method"] == "GET"
    assert sess.calls[0]["url"].endswith("/portfolio/orders/o1/queue_position")
    assert sess.calls[1]["url"].endswith("/portfolio/orders/o2/queue_position")


def test_get_queue_positions_csv_and_float_parsing(pem):
    body = {"queue_positions": [
        {"order_id": "o1", "market_ticker": "A", "queue_position_fp": "0.00"},
        {"order_id": "o2", "market_ticker": "B", "queue_position_fp": "4841.00"},
    ]}
    c, sess = _client(pem, [_Resp(200, body)])
    out = c.get_queue_positions(["A", "B"])
    assert sess.calls[0]["url"].endswith("/portfolio/orders/queue_positions")
    assert sess.calls[0]["params"] == {"market_tickers": "A,B"}
    assert out == [
        {"order_id": "o1", "market_ticker": "A", "queue_position_fp": 0.0},
        {"order_id": "o2", "market_ticker": "B", "queue_position_fp": 4841.0},
    ]
    assert all(isinstance(e["queue_position_fp"], float) for e in out)


def test_get_queue_positions_accepts_a_single_ticker_string(pem):
    c, sess = _client(pem, [_Resp(200, {"queue_positions": []})])
    assert c.get_queue_positions("ONLY") == []
    assert sess.calls[0]["params"] == {"market_tickers": "ONLY"}


# --------------------------------------------------------------------------- #
# 4. Amend: preserving vs. forfeiting, encoded as separate validated methods
# --------------------------------------------------------------------------- #

def test_amend_order_posts_fixed_point_payload(pem):
    c, sess = _client(pem, [_Resp(200, {"order_id": "o1", "remaining_count": "1.00"})])
    c.amend_order("o1", ticker="T", book_side="bid", price=0.56, count=10,
                  client_order_id="c1", updated_client_order_id="c2")
    (call,) = sess.calls
    assert call["method"] == "POST"
    assert call["url"].endswith("/portfolio/events/orders/o1/amend")
    assert call["json"] == {"ticker": "T", "side": "bid", "price": "0.5600",
                            "count": "10.00", "client_order_id": "c1",
                            "updated_client_order_id": "c2"}


def test_amend_size_down_preserves_queue_and_resends_price_unchanged(pem):
    c, sess = _client(pem, [_Resp(200, {"order_id": "o1"})])
    c.amend_size_down("o1", ticker="T", book_side="bid", price=0.85,
                      current_count=2, new_count=1)
    body = sess.calls[0]["json"]
    assert body["price"] == "0.8500" and body["count"] == "1.00"
    # The preserving wrapper has no way to express a price change.
    assert "new_price" not in inspect.signature(KalshiClient.amend_size_down).parameters
    assert "preserv" in KalshiClient.amend_size_down.__doc__.lower()


def test_amend_size_down_rejects_a_size_increase(pem):
    c, sess = _client(pem, [])
    with pytest.raises(ValueError):
        c.amend_size_down("o1", ticker="T", book_side="bid", price=0.85,
                          current_count=1, new_count=2)
    with pytest.raises(ValueError):
        c.amend_size_down("o1", ticker="T", book_side="bid", price=0.85,
                          current_count=2, new_count=2)
    assert sess.calls == []  # rejected before any request


def test_amend_expiry_preserves_queue_and_resends_price_and_count_unchanged(pem):
    c, sess = _client(pem, [_Resp(200, {"order_id": "o1"})])
    c.amend_expiry("o1", ticker="T", book_side="ask", price=0.15, count=2,
                   expiration_time=1795000000)
    body = sess.calls[0]["json"]
    assert body == {"ticker": "T", "side": "ask", "price": "0.1500", "count": "2.00",
                    "expiration_time": 1795000000}
    assert "preserv" in KalshiClient.amend_expiry.__doc__.lower()


def test_amend_price_forfeits_queue(pem):
    c, sess = _client(pem, [_Resp(200, {"order_id": "o1"})])
    c.amend_price("o1", ticker="T", book_side="bid", current_price=0.85,
                  new_price=0.84, count=2)
    body = sess.calls[0]["json"]
    assert body["price"] == "0.8400" and body["count"] == "2.00"
    assert "forfeit" in KalshiClient.amend_price.__doc__.lower()
    with pytest.raises(ValueError):
        c.amend_price("o1", ticker="T", book_side="bid", current_price=0.85,
                      new_price=0.85, count=2)


def test_amend_size_up_forfeits_queue_and_rejects_a_decrease(pem):
    c, sess = _client(pem, [_Resp(200, {"order_id": "o1"})])
    c.amend_size_up("o1", ticker="T", book_side="bid", price=0.85,
                    current_count=1, new_count=3)
    assert sess.calls[0]["json"]["count"] == "3.00"
    assert "forfeit" in KalshiClient.amend_size_up.__doc__.lower()
    with pytest.raises(ValueError):
        c.amend_size_up("o1", ticker="T", book_side="bid", price=0.85,
                        current_count=3, new_count=1)


# --------------------------------------------------------------------------- #
# 5. Read-after-write lag is documented where resting/queue methods live
# --------------------------------------------------------------------------- #

def test_read_after_write_lag_is_documented_on_resting_paths():
    assert "client_order_id" in KalshiClient.create_order.__doc__
    assert "lag" in KalshiClient.create_order.__doc__
    assert "lag" in KalshiVenue.submit_resting.__doc__
    assert "client_order_id" in KalshiVenue.submit_resting.__doc__
