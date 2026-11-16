"""
ev.ownbettor.keys -- venue credential resolution for the LIVE path only.

Order of resolution:
  1. Runtime environment: OWNBETTOR_KALSHI_KEY_ID plus OWNBETTOR_KALSHI_PRIVATE_KEY_PEM
     (or OWNBETTOR_KALSHI_PRIVATE_KEY_FILE pointing at a PEM file).
  2. GCP Secret Manager in the vault project (default beezy-vault-prod, override
     OWNBETTOR_VAULT_PROJECT): secrets kalshi-api-key-id and kalshi-private-key-pem.

Values are never logged, never returned in errors, and never read at all in dry-run
mode.  Luc provisions the real keys later through the secure flow; that step is not
part of this build.  Any failure resolves to None (the live adapter then refuses with
a "credentials not provisioned" error that names no value).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

VAULT_PROJECT_DEFAULT = "beezy-vault-prod"
_SECRET_KEY_ID = "kalshi-api-key-id"
_SECRET_PEM = "kalshi-private-key-pem"


@dataclass(frozen=True)
class Credentials:
    key_id: str
    private_key_pem: bytes
    source: str                    # 'env' | 'secret-manager' -- the only thing ever logged


def _from_env(env) -> Credentials | None:
    key_id = env.get("OWNBETTOR_KALSHI_KEY_ID")
    pem = env.get("OWNBETTOR_KALSHI_PRIVATE_KEY_PEM")
    pem_file = env.get("OWNBETTOR_KALSHI_PRIVATE_KEY_FILE")
    if pem_file and not pem:
        try:
            with open(os.path.expanduser(pem_file), "rb") as f:
                pem = f.read().decode()
        except OSError:
            return None
    if key_id and pem:
        return Credentials(key_id.strip(), pem.encode(), "env")
    return None


def _from_secret_manager(env) -> Credentials | None:
    try:
        from google.cloud import secretmanager
    except ImportError:
        return None
    project = env.get("OWNBETTOR_VAULT_PROJECT") or VAULT_PROJECT_DEFAULT
    try:
        client = secretmanager.SecretManagerServiceClient()
        def grab(name: str) -> str:
            resp = client.access_secret_version(
                name=f"projects/{project}/secrets/{name}/versions/latest")
            return resp.payload.data.decode()
        key_id, pem = grab(_SECRET_KEY_ID), grab(_SECRET_PEM)
    except Exception:                                        # noqa: BLE001 -- any vault failure = unprovisioned
        return None
    if key_id and pem:
        return Credentials(key_id.strip(), pem.encode(), "secret-manager")
    return None


def load_kalshi_credentials(env=None) -> Credentials | None:
    env = os.environ if env is None else env
    return _from_env(env) or _from_secret_manager(env)
