"""ev.novig.setup: key generation, env handling, subaccount/transfer request shapes (no network)."""
import json
import os
import stat

import pytest

from ev.novig import setup as ns
from ev.novig.client import NovigAPIError, NovigClient


class _R:
    def __init__(self, status, js):
        self.status_code, self._js, self.headers, self.content, self.text = status, js, {}, b"x", json.dumps(js)

    def json(self):
        return self._js


class _S:
    def __init__(self, seq):
        self.seq, self.headers, self.sent = list(seq), {}, []

    def request(self, method, url, data=None, headers=None, timeout=None):
        self.sent.append((method, url, data))
        return self.seq.pop(0)


def _mgmt(seq):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    pem = Ed25519PrivateKey.generate().private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                    serialization.NoEncryption())
    s = _S(seq)
    return NovigClient("paper", "mgmt-id", pem, session=s), s


def test_generated_trading_key_is_private_ed25519_and_never_overwritten(tmp_path):
    p = str(tmp_path / "t.pem")
    pub = ns.generate_trading_keypair(p)
    assert pub.startswith("-----BEGIN PUBLIC KEY-----") and "PRIVATE" not in pub
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600
    assert open(p).read().startswith("-----BEGIN PRIVATE KEY-----")
    with pytest.raises(SystemExit):
        ns.generate_trading_keypair(p)


def test_env_round_trip_is_owner_only(tmp_path):
    p = str(tmp_path / "paper.env")
    ns.write_env({"NOVIG_PAPER_KEY_ID": "abc", "X": "1"}, p)
    assert ns.read_env(p) == {"NOVIG_PAPER_KEY_ID": "abc", "X": "1"}
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600


def test_open_subaccount_registers_only_the_public_key_and_records_the_trading_key_id(tmp_path, monkeypatch):
    monkeypatch.setattr(ns, "TRADING_KEY_PATH", str(tmp_path / "paper_trading.pem"))
    monkeypatch.setattr(ns, "ENV_PATH", str(tmp_path / "paper.env"))
    mc, s = _mgmt([_R(201, {"keyId": "11111111-1111-1111-1111-111111111111", "label": "ev", "balance": "0.00000"})])
    r = ns.open_subaccount(mc, "ev", {"NOVIG_PAPER_KEY_ID": "mgmt-id"})
    body = json.loads(s.sent[0][2])
    assert s.sent[0][0] == "POST" and s.sent[0][1].endswith("/v3/account/subaccounts")
    assert body["algorithm"] == "Ed25519" and body["publicKey"].startswith("-----BEGIN PUBLIC KEY-----")
    assert "PRIVATE" not in json.dumps(body)
    env = ns.read_env(str(tmp_path / "paper.env"))
    assert env["NOVIG_PAPER_TRADING_KEY_ID"] == r["keyId"] and env["NOVIG_PAPER_KEY_ID"] == "mgmt-id"
    assert os.path.exists(str(tmp_path / "paper_trading.pem"))


def test_failed_registration_leaves_no_orphan_key(tmp_path, monkeypatch):
    kp = str(tmp_path / "paper_trading.pem")
    monkeypatch.setattr(ns, "TRADING_KEY_PATH", kp)
    monkeypatch.setattr(ns, "ENV_PATH", str(tmp_path / "paper.env"))
    mc, _ = _mgmt([_R(400, {"code": "BAD", "message": "no"})])
    with pytest.raises(NovigAPIError):
        ns.open_subaccount(mc, "ev", {})
    assert not os.path.exists(kp) and not os.path.exists(str(tmp_path / "paper.env"))


def test_fund_sends_fund_direction_five_decimals_and_an_idempotency_key():
    mc, s = _mgmt([_R(200, {"transferId": "t", "status": "Applied", "direction": "fund", "amount": "1000.00000"})])
    ns.fund(mc, "22222222-2222-2222-2222-222222222222", "1000.00000")
    body = json.loads(s.sent[0][2])
    assert body["direction"] == "fund" and body["amount"] == "1000.00000" and body["clientTransferId"].startswith("fund-")
    assert s.sent[0][1].endswith("/v3/account/subaccounts/22222222-2222-2222-2222-222222222222/transfer")
