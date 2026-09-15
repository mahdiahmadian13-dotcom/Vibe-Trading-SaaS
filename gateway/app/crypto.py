"""Vibe-Trading SaaS — encrypted server credentials (fleet R2).

SSH passwords / private keys for joined servers are stored ONLY as
Fernet ciphertext in server_nodes.ssh_secret. The master key lives in
the FLEET_MASTER_KEY env var (never in the DB, never in logs).

Per-server key derivation: HKDF(master, salt=server_id) → per-server
Fernet key, so leaking one row does not expose the others.
Rotation: CREDENTIALS_KEYS="kid2:newkey,kid1:oldkey" — newest encrypts,
all are tried for decrypt; ssh_key_id column records which kid wrote
each row; a one-shot job re-encrypts old rows.
"""

from __future__ import annotations

import base64
import os

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


def _master_keys() -> list[tuple[str, bytes]]:
    """Parse FLEET_MASTER_KEY / CREDENTIALS_KEYS into [(kid, raw32)].

    Formats accepted:
      - bare Fernet key (one key, kid="k1")
      - "kid2:<fernet>,kid1:<fernet>" (first = newest = used for encrypt)
    """
    raw = os.getenv("CREDENTIALS_KEYS") or os.getenv("FLEET_MASTER_KEY") or ""
    out: list[tuple[str, bytes]] = []
    for i, part in enumerate(p.strip() for p in raw.split(",") if p.strip()):
        if ":" in part:
            kid, key = part.split(":", 1)
        else:
            kid, key = f"k{i + 1}", part
        try:
            out.append((kid.strip(), base64.urlsafe_b64decode(key.strip())))
        except Exception:
            continue
    return out


def _per_server_fernet(master_raw: bytes, server_id: int) -> Fernet:
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=str(server_id).encode(),
        info=b"vibe-fleet-ssh",
    )
    return Fernet(base64.urlsafe_b64encode(hkdf.derive(master_raw)))


def encrypt_secret(plaintext: str, server_id: int) -> tuple[str, str]:
    """Encrypt → (ciphertext, kid). Raises RuntimeError if no master key."""
    keys = _master_keys()
    if not keys:
        raise RuntimeError("FLEET_MASTER_KEY is not set — cannot store SSH credentials")
    kid, raw = keys[0]
    token = _per_server_fernet(raw, server_id).encrypt(plaintext.encode())
    return token.decode(), kid


def decrypt_secret(ciphertext: str, server_id: int) -> str:
    """Try all known master keys (newest first). Raises ValueError if none work."""
    keys = _master_keys()
    if not keys:
        raise RuntimeError("FLEET_MASTER_KEY is not set — cannot decrypt SSH credentials")
    for _kid, raw in keys:
        try:
            return _per_server_fernet(raw, server_id).decrypt(ciphertext.encode()).decode()
        except InvalidToken:
            continue
    raise ValueError("SSH credential cannot be decrypted with any known master key")
