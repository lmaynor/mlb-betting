"""
ev.novig.setup -- one-time PAPER sandbox setup helper (management key -> subaccount + trading key -> funding).

  python -m ev.novig.setup echo                 # verify the management key signs correctly (POST /v3/echo)
  python -m ev.novig.setup balance              # primary wallet balance (paper)
  python -m ev.novig.setup open-subaccount      # generate a trading keypair LOCALLY, register it, save the key id
  python -m ev.novig.setup fund --amount 1000   # move paper money from the wallet into the subaccount
  python -m ev.novig.setup status

Credentials live in ~/.novig/paper.env (NOVIG_PAPER_KEY_ID, NOVIG_PAPER_PRIVATE_KEY_PATH, and after open-subaccount
NOVIG_PAPER_TRADING_KEY_ID / NOVIG_PAPER_TRADING_KEY_PATH). Private keys never print. Only the PAPER environment is
reachable (ev.novig.client refuses anything else). The trading key is created fresh here, never reused from the
management key, per Novig's model (the management key cannot place orders; a trading key cannot move money).
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid

from ev.novig.client import NovigAPIError, NovigClient

HOME = os.path.expanduser("~/.novig")
ENV_PATH = os.path.join(HOME, "paper.env")
TRADING_KEY_PATH = os.path.join(HOME, "paper_trading.pem")


def read_env(path: str | None = None) -> dict:
    path = path or ENV_PATH
    out = {}
    if os.path.exists(path):
        for line in open(path):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    return out


def write_env(env: dict, path: str | None = None) -> None:
    path = path or ENV_PATH
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    with open(path, "w") as fh:
        for k, v in env.items():
            fh.write(f"{k}={v}\n")
    os.chmod(path, 0o600)


def mgmt_client(env: dict | None = None, **kw) -> NovigClient:
    env = env if env is not None else read_env()
    kid, kp = env.get("NOVIG_PAPER_KEY_ID"), os.path.expanduser(env.get("NOVIG_PAPER_PRIVATE_KEY_PATH", ""))
    if not kid:
        raise SystemExit("NOVIG_PAPER_KEY_ID missing in ~/.novig/paper.env (the key id shown beside the key in Novig)")
    if not os.path.exists(kp):
        raise SystemExit(f"management key file not found: {kp}")
    return NovigClient("paper", kid, open(kp, "rb").read(), **kw)


def trading_client(env: dict | None = None, **kw) -> NovigClient:
    env = env if env is not None else read_env()
    kid, kp = env.get("NOVIG_PAPER_TRADING_KEY_ID"), os.path.expanduser(env.get("NOVIG_PAPER_TRADING_KEY_PATH", ""))
    if not kid or not os.path.exists(kp):
        raise SystemExit("no trading key yet: run `python -m ev.novig.setup open-subaccount`")
    return NovigClient("paper", kid, open(kp, "rb").read(), **kw)


def generate_trading_keypair(path: str | None = None) -> str:
    """Fresh Ed25519 keypair; private key saved 0600 (refuses to overwrite); returns the SPKI public PEM."""
    path = path or TRADING_KEY_PATH
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    if os.path.exists(path):
        raise SystemExit(f"{path} already exists; refusing to overwrite a trading key")
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(pem)
    return key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()


def open_subaccount(mc: NovigClient, label: str, env: dict) -> dict:
    pub = generate_trading_keypair()
    try:
        resp = mc.request("POST", "/v3/account/subaccounts", body={"label": label, "publicKey": pub, "algorithm": "Ed25519"},
                          signed=True)
    except NovigAPIError:
        os.remove(TRADING_KEY_PATH)                      # registration failed: do not leave an orphan key behind
        raise
    env = dict(env, NOVIG_PAPER_TRADING_KEY_ID=resp["keyId"], NOVIG_PAPER_TRADING_KEY_PATH=TRADING_KEY_PATH)
    write_env(env)
    return resp


def fund(mc: NovigClient, trading_key_id: str, amount: str) -> dict:
    return mc.request("POST", f"/v3/account/subaccounts/{trading_key_id}/transfer", signed=True,
                      body={"direction": "fund", "amount": amount, "clientTransferId": f"fund-{uuid.uuid4().hex[:20]}"})


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Novig PAPER setup")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("echo")
    sub.add_parser("balance")
    o = sub.add_parser("open-subaccount")
    o.add_argument("--label", default="ev-props")
    f = sub.add_parser("fund")
    f.add_argument("--amount", required=True, help="dollars of paper money, e.g. 1000")
    sub.add_parser("status")
    a = ap.parse_args(argv)
    env = read_env()
    try:
        if a.cmd == "echo":
            print("echo ok:", mgmt_client(env).echo())
        elif a.cmd == "balance":
            print("wallet:", mgmt_client(env).request("GET", "/v3/account/balance", signed=True))
        elif a.cmd == "open-subaccount":
            if env.get("NOVIG_PAPER_TRADING_KEY_ID"):
                raise SystemExit("a trading key is already configured; refusing to open another")
            r = open_subaccount(mgmt_client(env), a.label, env)
            print("subaccount opened:", {k: r[k] for k in ("keyId", "label", "balance") if k in r})
        elif a.cmd == "fund":
            if not env.get("NOVIG_PAPER_TRADING_KEY_ID"):
                raise SystemExit("no subaccount yet: run open-subaccount first")
            print("transfer:", fund(mgmt_client(env), env["NOVIG_PAPER_TRADING_KEY_ID"], f"{float(a.amount):.5f}"))
        else:
            mc = mgmt_client(env)
            print("subaccounts:", mc.request("GET", "/v3/account/subaccounts", signed=True))
            print("wallet:", mc.request("GET", "/v3/account/balance", signed=True))
    except NovigAPIError as e:
        print("API error:", e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
