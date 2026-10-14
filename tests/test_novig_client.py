"""ev.novig: NOVIG-V3 signing against the 30 official vectors, price grid, fees, book math, safety rails."""
import base64
import json
import os
from decimal import Decimal

import pytest

from ev.novig import signing
from ev.novig.client import (NovigAPIError, NovigClient, NovigProductionNotEnabled, Book, contract_cost, snap_price,
                             taker_fee)

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "ev", "novig_signing_vectors.json")
V = json.load(open(FIX))


def _key(kp_id):
    return signing.load_ed25519(V["keypairs"][kp_id]["private_key_pkcs8_pem"].encode())


@pytest.mark.parametrize("vec", [v for v in V["vectors"] if v["algorithm"] == "ed25519"], ids=lambda v: v["id"])
def test_ed25519_vectors_string_hash_and_signature(vec):
    i = vec["input"]
    body = i["body"].encode() if isinstance(i["body"], str) else json.dumps(i["body"]).encode()
    s = signing.string_to_sign(i["timestamp"], i["method"], i["path"], i["query"], body)
    assert s == vec["string_to_sign"]
    assert signing.body_hash(body) == vec["body_hash"]
    assert signing.sign(_key(vec["keypair_id"]), s) == vec["signature"]          # Ed25519 is deterministic


def test_all_vectors_with_p256_still_match_the_canonical_string():
    for vec in V["vectors"]:
        i = vec["input"]
        body = i["body"].encode() if isinstance(i["body"], str) else json.dumps(i["body"]).encode()
        assert signing.string_to_sign(i["timestamp"], i["method"], i["path"], i["query"], body) == vec["string_to_sign"], vec["id"]


def test_canonical_query_rules():
    assert signing.canonical_query("") == ""
    assert signing.canonical_query("status=open&limit=50") == "limit=50&status=open"
    assert signing.canonical_query("Z=1&a=2") == "Z=1&a=2"                          # 'Z' sorts before 'a'
    assert signing.canonical_query("a=b+c") == "a=b%2Bc"                            # a bare '+' stays literal '+'
    assert signing.canonical_query("k") == "k="


# ---------------- price grid / fees / money ----------------
@pytest.mark.parametrize("p,down,up", [(0.4973, "0.495", "0.500"), (0.0333, "0.033", "0.034"), (0.9532, "0.953", "0.954"),
                                       (0.5, "0.500", "0.500"), (0.0504, "0.050", "0.055"), (0.9, "0.900", "0.900"), (0.9475, "0.945", "0.950"), (0.0, "0.001", "0.001"), (1.0, "0.999", "0.999")])
def test_snap_price_three_bands(p, down, up):
    assert snap_price(p, "down") == down and snap_price(p, "up") == up


def test_fee_formula_matches_docs_examples_and_is_free_pregame():
    assert taker_fee(0.03, 0.50, 10_000, live=True) == pytest.approx(0.75)           # docs: 10,000 contracts @0.50
    assert taker_fee(0.03, 0.30, 10_000, live=True) == pytest.approx(0.63)
    assert taker_fee(0.03, 0.10, 10_000, live=True) == pytest.approx(0.27)
    assert taker_fee(0.06, 0.50, 10_000, live=True) == pytest.approx(1.50)
    assert taker_fee(0.03, 0.50, 10_000, live=False) == 0.0                          # WHEN_LIVE: pre-game is free
    assert taker_fee(0.06, 0.50, 10_000, live=False, charged="ALWAYS") == pytest.approx(1.50)


def test_contract_cost_is_price_times_qty_times_a_cent():
    assert contract_cost(0.665, 1000) == pytest.approx(6.65)


def test_book_asks_are_the_complement_of_the_other_sides_bids():
    b = Book("m", {"over": [{"price": "0.345", "qty": 545771}, {"price": "0.34", "qty": 5}],
                   "under": [{"price": "0.64", "qty": 355800}, {"price": "0.63", "qty": 10}]})
    assert b.asks("over", "under")[0] == (Decimal("0.36"), 355800)                  # buy Over @ 1-0.64
    assert b.asks("under", "over")[0] == (Decimal("0.655"), 545771)
    assert b.best_bid("over") == Decimal("0.345")


# ---------------- safety rails ----------------
def test_production_and_unknown_envs_are_refused():
    for env in ("prod", "production", "live"):
        with pytest.raises(NovigProductionNotEnabled):
            NovigClient(env)
    with pytest.raises(NovigProductionNotEnabled):
        NovigClient("public-data", key_id="x")


def test_public_client_can_only_get_public_paths():
    c = NovigClient("public-data")
    for m, p in (("POST", "/v3/public/catalog/markets"), ("GET", "/v3/orders"), ("DELETE", "/v3/public/x")):
        with pytest.raises(NovigProductionNotEnabled):
            c.request(m, p)


class _R:
    def __init__(self, status, js=None, headers=None):
        self.status_code, self._js, self.headers = status, js or {}, headers or {}
        self.content, self.text = (b"x" if js is not None else b""), json.dumps(js or {})
    def json(self):
        return self._js


class _S:
    def __init__(self, seq):
        self.seq, self.calls, self.headers, self.last = list(seq), 0, {}, None
    def request(self, method, url, data=None, headers=None, timeout=None):
        self.calls += 1
        self.last = (method, url, data, headers)
        return self.seq.pop(0)


def _paper(seq, monkeypatch):
    import ev.novig.client as nc
    monkeypatch.setattr(nc.time, "sleep", lambda *_: None)
    pem = V["keypairs"]["ed25519-test-1"]["private_key_pkcs8_pem"].encode()
    s = _S(seq)
    return NovigClient("paper", "key-uuid", pem, session=s, clock=lambda: 1755000000.0), s


def test_signed_post_sends_exact_bytes_content_type_and_verifiable_signature(monkeypatch):
    c, s = _paper([_R(200, {"orderId": "o1"})], monkeypatch)
    c.place_order("oc-uuid", 0.4973, 100, "IOC", client_id="11111111-1111-1111-1111-111111111111")
    method, url, data, h = s.last
    assert method == "POST" and url == "https://api.paper.novig.com/v3/orders"
    assert h["Content-Type"] == "application/json" and h["Novig-Key-Id"] == "key-uuid"
    sent = json.loads(data)
    assert sent["price"] == "0.495" and sent["qty"] == 100 and sent["tif"] == "IOC"
    # signature covers the EXACT body bytes that were sent
    expect = signing.sign(c._key, signing.string_to_sign(1755000000000, "POST", "/v3/orders", "", data))
    assert h["Novig-Signature"] == expect and h["Novig-Timestamp"] == "1755000000000"


def test_post_is_never_retried_but_get_429_is(monkeypatch):
    c, s = _paper([_R(500, {"code": "X", "message": "boom"}), _R(200, {"orderId": "o"})], monkeypatch)
    with pytest.raises(NovigAPIError):
        c.place_order("oc", 0.5, 10, "IOC")
    assert s.calls == 1                                              # a replayed place always places again
    c2, s2 = _paper([_R(429, {"code": "RATE_LIMIT_EXCEEDED"}, {"Retry-After": "1"}), _R(200, {"items": [], "next": None})], monkeypatch)
    assert c2.list_orders() == {"items": [], "next": None} and s2.calls == 2


def test_bad_tif_rejected_locally(monkeypatch):
    c, _ = _paper([], monkeypatch)
    with pytest.raises(ValueError):
        c.place_order("oc", 0.5, 1, "DAY")
