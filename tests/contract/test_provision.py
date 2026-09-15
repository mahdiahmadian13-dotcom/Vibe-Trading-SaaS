"""Contract tests — provision endpoints (T014, US9).

Run against the live gateway: BASE=http://127.0.0.1:9001 .venv/bin/python -m pytest tests/contract/test_provision.py -q
Requires admin JWT in VT_ADMIN_TOKEN env (login mahdi).
"""

import os

import pytest
import requests

BASE = os.getenv("VT_BASE", "http://127.0.0.1:9001")
TOKEN = os.getenv("VT_ADMIN_TOKEN", "")
# NOTE: when running locally without VT_ADMIN_TOKEN, export the real admin
# password: VT_ADMIN_PASS=<from server .env> (never commit it).


def _h(token=TOKEN):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _login():
    r = requests.post(f"{BASE}/api/v1/auth/login", json={"username": "mahdi", "password": os.getenv("VT_ADMIN_PASS", "VtAdminE2E2026!")}, timeout=15)
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


@pytest.fixture(scope="module")
def admin_headers():
    tok = TOKEN or _login()
    return _h(tok)


def test_provision_requires_secret(admin_headers):
    r = requests.post(f"{BASE}/api/v1/admin/fleet/servers",
                      json={"name": "t-no-secret", "ssh_host": "10.0.0.99", "auth_type": "password"},
                      headers=admin_headers, timeout=20)
    assert r.status_code == 400
    assert "رمز عبور" in r.text


def test_provision_status_404(admin_headers):
    r = requests.get(f"{BASE}/api/v1/admin/fleet/servers/999999/provision", headers=admin_headers, timeout=20)
    assert r.status_code == 404


def test_retry_no_job_404(admin_headers):
    r = requests.post(f"{BASE}/api/v1/admin/fleet/servers/999999/provision/retry", headers=admin_headers, timeout=20)
    assert r.status_code == 404
