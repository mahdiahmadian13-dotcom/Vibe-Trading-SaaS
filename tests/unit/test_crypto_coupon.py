"""Unit tests — fleet foundational: crypto + coupon policy (T013).

Run: .venv/bin/python -m pytest tests/unit/test_crypto_coupon.py -q
"""

import os
import sys
from datetime import timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

TEST_MASTER = "7aD5m2k9Q8w1xY4vB6nH0oP3rS5tU7vW9yA1cE3gI5kM7oQ9sU1wY3=="


def _load_crypto():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "fleet_crypto", "gateway/app/crypto.py"
    )
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_crypto_roundtrip():
    os.environ["FLEET_MASTER_KEY"] = TEST_MASTER
    os.environ.pop("CREDENTIALS_KEYS", None)
    c = _load_crypto()
    ct, kid = c.encrypt_secret("s3cr3t-pass", 7)
    assert ct != "s3cr3t-pass"
    assert "s3cr3t" not in ct
    assert c.decrypt_secret(ct, 7) == "s3cr3t-pass"


def test_crypto_per_server_isolation():
    os.environ["FLEET_MASTER_KEY"] = TEST_MASTER
    os.environ.pop("CREDENTIALS_KEYS", None)
    c = _load_crypto()
    ct7, _ = c.encrypt_secret("same-secret", 7)
    ct8, _ = c.encrypt_secret("same-secret", 8)
    assert ct7 != ct8  # different salt → different ciphertext
    with pytest.raises(ValueError):
        c.decrypt_secret(ct7, 8)  # wrong server → InvalidToken → ValueError


def test_crypto_rotation_decrypt():
    os.environ["FLEET_MASTER_KEY"] = TEST_MASTER
    os.environ.pop("CREDENTIALS_KEYS", None)
    c = _load_crypto()
    ct, _ = c.encrypt_secret("rotate-me", 3)
    # rotate: new kid first, old second — old rows still decrypt
    os.environ["CREDENTIALS_KEYS"] = f"kid2:{TEST_MASTER},kid1:{TEST_MASTER}"
    assert c.decrypt_secret(ct, 3) == "rotate-me"
    os.environ.pop("CREDENTIALS_KEYS", None)


def test_crypto_no_master_raises():
    os.environ.pop("FLEET_MASTER_KEY", None)
    os.environ.pop("CREDENTIALS_KEYS", None)
    c = _load_crypto()
    with pytest.raises(RuntimeError):
        c.encrypt_secret("x", 1)


# --- coupon policy (pure-logic part: ordering + expiry) ---


def _sort_like_consume(coupons, now):
    usable = [x for x in coupons if x["expires_at"] is None or x["expires_at"] > now]
    usable.sort(key=lambda x: (x["expires_at"] is None, x["created"]))
    return usable


def test_consume_order_daily_before_permanent():
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    coins = [
        {"source": "bonus", "expires_at": None, "created": 1},
        {"source": "daily", "expires_at": now + timedelta(hours=5), "created": 2},
    ]
    first = _sort_like_consume(coins, now)[0]
    assert first["source"] == "daily"


def test_expired_daily_not_usable():
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    coins = [
        {"source": "daily", "expires_at": now - timedelta(hours=1), "created": 1},
        {"source": "referral", "expires_at": None, "created": 2},
    ]
    usable = _sort_like_consume(coins, now)
    assert len(usable) == 1 and usable[0]["source"] == "referral"
