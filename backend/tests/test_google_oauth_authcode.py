"""Iteration 11 — Google OAuth 2.0 Authorization-Code flow tests + auth regression.

Covers: /api/auth/config, /api/auth/google/start, /api/auth/google/callback error paths,
state single-use/CSRF, no client-secret leaks, and regression on existing auth endpoints.
"""
import os
import re
import subprocess
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import pytest
import requests
from dotenv import dotenv_values

frontend_env = dotenv_values("/app/frontend/.env")
backend_env = dotenv_values("/app/backend/.env")
base_url = os.environ.get("REACT_APP_BACKEND_URL") or frontend_env.get("REACT_APP_BACKEND_URL")
if not base_url:
    raise RuntimeError("REACT_APP_BACKEND_URL is missing")
BASE_URL = base_url.rstrip("/")
API = f"{BASE_URL}/api"

CLIENT_ID = (backend_env.get("GOOGLE_CLIENT_ID") or "").strip()
CLIENT_SECRET = (backend_env.get("GOOGLE_CLIENT_SECRET") or "").strip()
REDIRECT_URI = (backend_env.get("GOOGLE_REDIRECT_URI") or "").strip()


@pytest.fixture(scope="module")
def client():
    s = requests.Session()
    return s


@pytest.fixture(scope="module")
def credentials():
    p = Path("/app/memory/test_credentials.md")
    if not p.exists():
        pytest.skip("missing test_credentials.md")
    txt = p.read_text()
    email = re.search(r"Email:\s*`([^`]+)`", txt)
    pwd = re.search(r"Password:\s*`([^`]+)`", txt)
    if not email or not pwd:
        pytest.skip("no creds parsed")
    return {"email": email.group(1), "password": pwd.group(1)}


@pytest.fixture(scope="module")
def token(client, credentials):
    r = client.post(f"{API}/auth/login", json=credentials)
    if r.status_code != 200:
        pytest.fail(f"login failed {r.status_code}: {r.text[:300]}")
    t = r.json().get("access_token")
    assert t
    return t


def fragment_params(location: str) -> dict:
    frag = location.split("#", 1)[1] if "#" in location else ""
    return {k: v[0] for k, v in parse_qs(frag).items()}


# ---------------- /api/auth/config ----------------
class TestAuthConfig:
    def test_config_exposes_authcode_flag_and_public_client_id(self, client):
        r = client.get(f"{API}/auth/config")
        assert r.status_code == 200
        data = r.json()
        assert data["google_authcode_enabled"] is True
        assert data["google_enabled"] is True
        assert data["google_client_id"] == CLIENT_ID
        assert data["google_client_id"].endswith(".apps.googleusercontent.com")

    def test_config_has_no_secret(self, client):
        r = client.get(f"{API}/auth/config")
        body = r.text
        assert CLIENT_SECRET not in body
        assert "GOCSPX" not in body
        assert not any("secret" in k.lower() for k in r.json().keys())


# ---------------- /api/auth/google/start ----------------
class TestGoogleStart:
    def test_start_redirects_to_google_with_correct_params(self, client):
        r = client.get(f"{API}/auth/google/start", allow_redirects=False)
        assert r.status_code == 302, r.text[:300]
        loc = r.headers["location"]
        parsed = urlparse(loc)
        assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == "https://accounts.google.com/o/oauth2/v2/auth"
        q = parse_qs(parsed.query)
        assert q["client_id"][0] == CLIENT_ID
        assert q["redirect_uri"][0] == REDIRECT_URI
        assert q["redirect_uri"][0].endswith("/api/auth/google/callback")
        assert q["response_type"][0] == "code"
        assert set(q["scope"][0].split()) == {"openid", "email", "profile"}
        assert len(q["state"][0]) >= 20
        # no secret in the outbound URL or headers
        assert CLIENT_SECRET not in loc
        assert "GOCSPX" not in str(r.headers)

    def test_start_sets_httponly_lax_secure_state_cookie(self, client):
        r = requests.get(f"{API}/auth/google/start", allow_redirects=False)
        state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
        raw_cookie = r.headers.get("set-cookie", "")
        assert "fin_g_state" in raw_cookie, raw_cookie
        assert f"fin_g_state={state}" in raw_cookie
        low = raw_cookie.lower()
        assert "httponly" in low
        assert "samesite=lax" in low
        assert "secure" in low

    def test_start_states_are_unique(self, client):
        states = set()
        for _ in range(3):
            r = requests.get(f"{API}/auth/google/start", allow_redirects=False)
            states.add(parse_qs(urlparse(r.headers["location"]).query)["state"][0])
        assert len(states) == 3

    def test_start_remembers_next_param(self, client):
        r = requests.get(f"{API}/auth/google/start?next=/goals", allow_redirects=False)
        assert r.status_code == 302
        state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
        # Consume the state with a bogus code -> redirect must preserve next=/goals
        cb = requests.get(
            f"{API}/auth/google/callback",
            params={"code": "bogus-code-xyz", "state": state},
            allow_redirects=False,
        )
        assert cb.status_code == 302
        loc = cb.headers["location"]
        assert "next=/goals" in loc, loc


# ---------------- /api/auth/google/callback error paths ----------------
class TestGoogleCallbackErrors:
    def test_user_cancellation(self, client):
        r = requests.get(f"{API}/auth/google/callback", params={"error": "access_denied"}, allow_redirects=False)
        assert r.status_code == 302
        loc = r.headers["location"]
        assert "/auth/google/success" in loc
        assert fragment_params(loc).get("error") == "google_cancelled"

    def test_generic_google_error(self, client):
        r = requests.get(f"{API}/auth/google/callback", params={"error": "server_error"}, allow_redirects=False)
        assert r.status_code == 302
        assert fragment_params(r.headers["location"]).get("error") == "google_error"

    def test_missing_code_or_state(self, client):
        r = requests.get(f"{API}/auth/google/callback", allow_redirects=False)
        assert r.status_code == 302
        assert fragment_params(r.headers["location"]).get("error") == "missing_code_or_state"

    def test_missing_state_only(self, client):
        r = requests.get(f"{API}/auth/google/callback", params={"code": "abc"}, allow_redirects=False)
        assert fragment_params(r.headers["location"]).get("error") == "missing_code_or_state"

    def test_unknown_state(self, client):
        r = requests.get(
            f"{API}/auth/google/callback",
            params={"code": "abc", "state": "totally-unknown-state-value-123456"},
            allow_redirects=False,
        )
        assert r.status_code == 302
        assert fragment_params(r.headers["location"]).get("error") == "invalid_state"

    def test_valid_state_bogus_code_fails_token_exchange_without_leaking(self, client):
        s = requests.get(f"{API}/auth/google/start", allow_redirects=False)
        state = parse_qs(urlparse(s.headers["location"]).query)["state"][0]
        r = requests.get(
            f"{API}/auth/google/callback",
            params={"code": "4/bogus-authorization-code", "state": state},
            allow_redirects=False,
        )
        assert r.status_code == 302
        frag = fragment_params(r.headers["location"])
        assert frag.get("error") == "token_exchange_failed", frag
        assert "access_token" not in frag
        loc = r.headers["location"]
        assert CLIENT_SECRET not in loc
        assert "invalid_grant" not in loc  # raw Google detail must not leak

    def test_state_is_single_use(self, client):
        s = requests.get(f"{API}/auth/google/start", allow_redirects=False)
        state = parse_qs(urlparse(s.headers["location"]).query)["state"][0]
        first = requests.get(f"{API}/auth/google/callback", params={"code": "bogus", "state": state}, allow_redirects=False)
        assert fragment_params(first.headers["location"]).get("error") == "token_exchange_failed"
        second = requests.get(f"{API}/auth/google/callback", params={"code": "bogus", "state": state}, allow_redirects=False)
        assert fragment_params(second.headers["location"]).get("error") == "invalid_state"

    def test_cookie_state_mismatch_rejected(self, client):
        s = requests.get(f"{API}/auth/google/start", allow_redirects=False)
        state = parse_qs(urlparse(s.headers["location"]).query)["state"][0]
        r = requests.get(
            f"{API}/auth/google/callback",
            params={"code": "bogus", "state": state},
            cookies={"fin_g_state": "some-other-attacker-state"},
            allow_redirects=False,
        )
        assert fragment_params(r.headers["location"]).get("error") == "state_mismatch"

    def test_callback_never_returns_5xx(self, client):
        for params in [{}, {"code": ""}, {"state": ""}, {"code": "x", "state": "y"}, {"error": "weird"}]:
            r = requests.get(f"{API}/auth/google/callback", params=params, allow_redirects=False)
            assert r.status_code < 500, (params, r.status_code)


# ---------------- Secret leak checks ----------------
class TestNoSecretLeak:
    def test_no_secret_in_any_auth_response(self, client):
        endpoints = [
            ("get", f"{API}/auth/config", {}),
            ("get", f"{API}/auth/google/start", {}),
            ("get", f"{API}/auth/google/callback?error=access_denied", {}),
            ("get", f"{API}/auth/me", {}),
        ]
        for method, url, kw in endpoints:
            r = getattr(requests, method)(url, allow_redirects=False, **kw)
            blob = r.text + str(r.headers) + str(r.cookies.get_dict())
            assert CLIENT_SECRET not in blob, url
            assert "GOCSPX" not in blob, url

    def test_no_secret_in_backend_logs(self):
        out = subprocess.run(
            "grep -c 'GOCSPX' /var/log/supervisor/backend.err.log /var/log/supervisor/backend.out.log || true",
            shell=True, capture_output=True, text=True,
        ).stdout
        for line in out.strip().splitlines():
            if ":" in line:
                assert line.rsplit(":", 1)[1] == "0", f"secret leaked in logs: {line}"


# ---------------- DB indexes ----------------
class TestIndexes:
    def test_oauth_states_indexes(self):
        script = (
            "import asyncio,os;from motor.motor_asyncio import AsyncIOMotorClient;"
            "from dotenv import dotenv_values;e=dotenv_values('/app/backend/.env');"
            "c=AsyncIOMotorClient(e['MONGO_URL']);d=c[e['DB_NAME']];"
            "print(asyncio.get_event_loop().run_until_complete(d.oauth_states.index_information()))"
        )
        r = subprocess.run(["python", "-c", script], capture_output=True, text=True, cwd="/app/backend")
        assert r.returncode == 0, r.stderr[-500:]
        info = r.stdout
        assert "'state_1'" in info, info
        assert "unique" in info, info
        assert "expireAfterSeconds" in info, info


# ---------------- Regression ----------------
class TestRegression:
    created_emails = []

    def test_register_new_user(self, client):
        import uuid
        email = f"TEST_g_{uuid.uuid4().hex[:8]}@qa.finaura.dev".lower()
        r = requests.post(f"{API}/auth/register", json={"email": email, "password": "testpass123", "name": "TEST Google"})
        assert r.status_code == 200, r.text[:300]
        data = r.json()
        assert data["token_type"] == "bearer"
        assert isinstance(data["access_token"], str) and data["access_token"]
        assert data["user"]["email"] == email
        assert "picture" in data["user"]
        assert "_id" not in data["user"]
        # cleanup
        d = requests.delete(f"{API}/auth/account", headers={"Authorization": f"Bearer {data['access_token']}"})
        assert d.status_code == 200

    def test_login_existing_user(self, client, credentials):
        r = requests.post(f"{API}/auth/login", json=credentials)
        assert r.status_code == 200, r.text[:300]
        assert r.json()["user"]["email"] == credentials["email"]

    def test_legacy_google_idtoken_endpoint_rejects_fake(self, client):
        r = requests.post(f"{API}/auth/google", json={"credential": "fake.credential.value"})
        assert r.status_code == 401, r.text[:300]
        assert "GOCSPX" not in r.text

    def test_me_includes_picture(self, token):
        r = requests.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        data = r.json()
        assert "picture" in data
        assert "id" in data and "_id" not in data

    def test_me_unauthenticated(self):
        r = requests.get(f"{API}/auth/me")
        assert r.status_code == 401

    def test_financial_overview(self, token):
        # NOTE: there is no GET /api/goals route; goals are returned inside /financial/overview
        h = {"Authorization": f"Bearer {token}"}
        o = requests.get(f"{API}/financial/overview", headers=h)
        assert o.status_code == 200, o.text[:300]
        data = o.json()
        assert isinstance(data, dict)
        assert "goals" in data
        assert isinstance(data["goals"], list)

    def test_goals_crud(self, token):
        h = {"Authorization": f"Bearer {token}"}
        c = requests.post(f"{API}/goals", headers=h, json={
            "name": "TEST_oauth_goal", "target_amount": 1000, "current_amount": 100,
            "deadline": "2027-01-01", "priority": "Medium",
        })
        assert c.status_code in (200, 201), c.text[:300]
        gid = c.json().get("id")
        assert gid
        p = requests.patch(f"{API}/goals/{gid}", headers=h, json={
            "name": "TEST_oauth_goal", "target_amount": 1000, "current_amount": 250,
            "deadline": "2027-01-01", "priority": "Medium",
        })
        assert p.status_code == 200, p.text[:300]
        ov = requests.get(f"{API}/financial/overview", headers=h).json()
        match = [g for g in ov["goals"] if g.get("id") == gid]
        assert match and match[0]["current_amount"] == 250
        d = requests.delete(f"{API}/goals/{gid}", headers=h)
        assert d.status_code == 200
        ov2 = requests.get(f"{API}/financial/overview", headers=h).json()
        assert not [g for g in ov2["goals"] if g.get("id") == gid]
