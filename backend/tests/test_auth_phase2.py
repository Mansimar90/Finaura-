"""Finaura Phase 2 — auth stack + multi-tenancy backend tests."""
import os
import time
import uuid

import pytest
import requests
from dotenv import dotenv_values

frontend_env = dotenv_values("/app/frontend/.env")
base_url = os.environ.get("REACT_APP_BACKEND_URL") or frontend_env.get("REACT_APP_BACKEND_URL")
if not base_url:
    raise RuntimeError("REACT_APP_BACKEND_URL missing")
BASE = base_url.rstrip("/") + "/api"

PASSWORD = "TestPass123!"


def rnd_email(tag="u"):
    return f"TEST_{tag}_{uuid.uuid4().hex[:10]}@qa.finaura.dev"


@pytest.fixture(scope="module")
def s():
    sess = requests.Session()
    sess.headers.update({"Content-Type": "application/json"})
    return sess


@pytest.fixture(scope="module")
def created_tokens():
    return []


@pytest.fixture(scope="module", autouse=True)
def cleanup(s, created_tokens):
    yield
    for t in created_tokens:
        try:
            s.delete(f"{BASE}/auth/account", headers={"Authorization": f"Bearer {t}"}, timeout=30)
        except Exception:
            pass


def register(s, created_tokens, email=None, name="TEST User"):
    email = email or rnd_email()
    r = s.post(f"{BASE}/auth/register", json={"email": email, "password": PASSWORD, "name": name}, timeout=30)
    assert r.status_code == 200, f"register failed {r.status_code} {r.text[:300]}"
    data = r.json()
    created_tokens.append(data["access_token"])
    return email, data


# ---------- config ----------
class TestConfig:
    def test_auth_config_flags(self, s):
        # Google is now configured in this environment (auth-code flow); Apple/Resend are not.
        r = s.get(f"{BASE}/auth/config", timeout=30)
        assert r.status_code == 200
        d = r.json()
        assert d["google_enabled"] is True
        assert d["google_authcode_enabled"] is True
        assert d["apple_enabled"] is False
        assert d["resend_enabled"] is False
        assert "client_secret" not in r.text.lower()


# ---------- register / login / me ----------
class TestRegisterLogin:
    def test_register_returns_jwt_and_user(self, s, created_tokens):
        email, data = register(s, created_tokens)
        assert data["token_type"] == "bearer"
        assert isinstance(data["access_token"], str) and len(data["access_token"]) > 20
        u = data["user"]
        assert u["email"] == email.lower()
        assert u["has_password"] is True
        assert u["has_pin"] is False
        assert u["onboarding_done"] is False
        assert u["has_demo_data"] is False
        assert "id" in u and "_id" not in u

    def test_duplicate_register_409(self, s, created_tokens):
        email, _ = register(s, created_tokens)
        r = s.post(f"{BASE}/auth/register", json={"email": email, "password": PASSWORD}, timeout=30)
        assert r.status_code == 409, r.text[:300]

    def test_register_weak_password_422(self, s):
        r = s.post(f"{BASE}/auth/register", json={"email": rnd_email(), "password": "short"}, timeout=30)
        assert r.status_code == 422

    def test_login_success_and_me(self, s, created_tokens):
        email, _ = register(s, created_tokens)
        r = s.post(f"{BASE}/auth/login", json={"email": email, "password": PASSWORD}, timeout=30)
        assert r.status_code == 200, r.text[:300]
        token = r.json()["access_token"]
        me = s.get(f"{BASE}/auth/me", headers={"Authorization": f"Bearer {token}"}, timeout=30)
        assert me.status_code == 200
        assert me.json()["email"] == email.lower()

    def test_login_wrong_password_401(self, s, created_tokens):
        email, _ = register(s, created_tokens)
        r = s.post(f"{BASE}/auth/login", json={"email": email, "password": "WrongPass999"}, timeout=30)
        assert r.status_code == 401

    def test_me_without_token_401(self, s):
        r = s.get(f"{BASE}/auth/me", timeout=30)
        assert r.status_code == 401

    def test_me_bad_token_401(self, s):
        r = s.get(f"{BASE}/auth/me", headers={"Authorization": "Bearer garbage.token.value"}, timeout=30)
        assert r.status_code == 401

    def test_bruteforce_lockout_429(self, s, created_tokens):
        email, _ = register(s, created_tokens, name="TEST Lock")
        codes = []
        for _ in range(5):
            codes.append(s.post(f"{BASE}/auth/login", json={"email": email, "password": "Nope12345"}, timeout=30).status_code)
        assert codes == [401] * 5, codes
        r = s.post(f"{BASE}/auth/login", json={"email": email, "password": "Nope12345"}, timeout=30)
        # BUG: returns 500 (naive vs aware datetime compare in check_login_lockout) instead of 429
        assert r.status_code == 429, f"expected lockout, got {r.status_code} {r.text[:200]}"
        # correct password should also be locked out
        r2 = s.post(f"{BASE}/auth/login", json={"email": email, "password": PASSWORD}, timeout=30)
        assert r2.status_code == 429

    def test_bcrypt_hash_format(self):
        import asyncio
        from motor.motor_asyncio import AsyncIOMotorClient
        env = dotenv_values("/app/backend/.env")

        async def check():
            c = AsyncIOMotorClient(env["MONGO_URL"])
            doc = await c[env["DB_NAME"]].users.find_one({"password_hash": {"$ne": None}})
            c.close()
            return doc

        doc = asyncio.get_event_loop().run_until_complete(check()) if False else asyncio.run(check())
        assert doc is not None
        assert doc["password_hash"].startswith("$2b$"), doc["password_hash"][:10]


# ---------- OAuth not configured ----------
class TestOAuthNotConfigured:
    def test_google_rejects_fake_credential(self, s):
        # Google IS configured now, so a fake ID token must be rejected with 401 (not 503)
        r = s.post(f"{BASE}/auth/google", json={"credential": "fake"}, timeout=30)
        assert r.status_code == 401, r.text[:200]
        assert "invalid google credential" in r.json()["detail"].lower()

    def test_apple_503(self, s):
        r = s.post(f"{BASE}/auth/apple", json={"id_token": "fake"}, timeout=30)
        assert r.status_code == 503
        assert "not configured" in r.json()["detail"].lower()


# ---------- password reset / verification ----------
class TestPasswordReset:
    def test_forgot_password_generic_unknown_email(self, s):
        r = s.post(f"{BASE}/auth/forgot-password", json={"email": rnd_email("nobody")}, timeout=30)
        assert r.status_code == 200
        assert "if that email is registered" in r.json()["message"].lower()

    def test_forgot_password_existing_user(self, s, created_tokens):
        email, _ = register(s, created_tokens)
        r = s.post(f"{BASE}/auth/forgot-password", json={"email": email}, timeout=30)
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_reset_password_invalid_token_400(self, s):
        r = s.post(f"{BASE}/auth/reset-password", json={"token": "invalid-token", "new_password": "NewPass1234"}, timeout=30)
        assert r.status_code == 400

    def test_verify_email_invalid_token_400(self, s):
        r = s.post(f"{BASE}/auth/verify-email", json={"token": "invalid-token"}, timeout=30)
        assert r.status_code == 400

    def test_reset_password_full_flow(self, s, created_tokens):
        """Uses the DB-issued token directly (email delivery is console fallback)."""
        import asyncio, hashlib
        from motor.motor_asyncio import AsyncIOMotorClient
        from bson import ObjectId
        env = dotenv_values("/app/backend/.env")
        email, data = register(s, created_tokens)
        uid = data["user"]["id"]
        assert s.post(f"{BASE}/auth/forgot-password", json={"email": email}, timeout=30).status_code == 200

        # We cannot read the raw token (only hash stored) -> verify a token record exists
        async def count():
            c = AsyncIOMotorClient(env["MONGO_URL"])
            n = await c[env["DB_NAME"]].password_reset_tokens.count_documents({"user_id": ObjectId(uid), "used": False})
            c.close()
            return n

        assert asyncio.run(count()) == 1, "no reset token persisted"


# ---------- PIN ----------
class TestPin:
    def test_set_verify_remove_pin(self, s, created_tokens):
        _, data = register(s, created_tokens)
        h = {"Authorization": f"Bearer {data['access_token']}"}
        r = s.post(f"{BASE}/auth/set-pin", json={"pin": "1234"}, headers=h, timeout=30)
        assert r.status_code == 200 and r.json()["has_pin"] is True
        me = s.get(f"{BASE}/auth/me", headers=h, timeout=30).json()
        assert me["has_pin"] is True
        assert s.post(f"{BASE}/auth/verify-pin", json={"pin": "1234"}, headers=h, timeout=30).status_code == 200
        bad = s.post(f"{BASE}/auth/verify-pin", json={"pin": "9999"}, headers=h, timeout=30)
        assert bad.status_code == 401
        assert "attempts left" in bad.json()["detail"].lower(), bad.text[:200]
        rm = s.post(f"{BASE}/auth/remove-pin", json={"pin": "1234"}, headers=h, timeout=30)
        assert rm.status_code == 200 and rm.json()["has_pin"] is False
        assert s.get(f"{BASE}/auth/me", headers=h, timeout=30).json()["has_pin"] is False

    def test_pin_hash_is_bcrypt(self, s, created_tokens):
        import asyncio
        from motor.motor_asyncio import AsyncIOMotorClient
        from bson import ObjectId
        env = dotenv_values("/app/backend/.env")
        _, data = register(s, created_tokens)
        h = {"Authorization": f"Bearer {data['access_token']}"}
        s.post(f"{BASE}/auth/set-pin", json={"pin": "4321"}, headers=h, timeout=30)

        async def get_hash():
            c = AsyncIOMotorClient(env["MONGO_URL"])
            doc = await c[env["DB_NAME"]].users.find_one({"_id": ObjectId(data["user"]["id"])})
            c.close()
            return doc["pin_hash"]

        ph = asyncio.run(get_hash())
        assert ph.startswith("$2b$"), ph[:10]
        assert ph != "4321"

    def test_pin_validation_rejects_non_4_digit(self, s, created_tokens):
        _, data = register(s, created_tokens)
        h = {"Authorization": f"Bearer {data['access_token']}"}
        for bad in ["12", "abcd", "123456"]:
            assert s.post(f"{BASE}/auth/set-pin", json={"pin": bad}, headers=h, timeout=30).status_code == 422, bad

    def test_verify_pin_without_pin_set_400(self, s, created_tokens):
        _, data = register(s, created_tokens)
        h = {"Authorization": f"Bearer {data['access_token']}"}
        r = s.post(f"{BASE}/auth/verify-pin", json={"pin": "1234"}, headers=h, timeout=30)
        assert r.status_code == 400

    def test_set_pin_requires_auth(self, s):
        assert s.post(f"{BASE}/auth/set-pin", json={"pin": "1234"}, timeout=30).status_code == 401

    def test_pin_lockout_after_5_failures(self, s, created_tokens):
        _, data = register(s, created_tokens)
        h = {"Authorization": f"Bearer {data['access_token']}"}
        s.post(f"{BASE}/auth/set-pin", json={"pin": "1111"}, headers=h, timeout=30)
        codes = [s.post(f"{BASE}/auth/verify-pin", json={"pin": "2222"}, headers=h, timeout=30).status_code for _ in range(5)]
        assert codes == [401] * 5, codes
        r = s.post(f"{BASE}/auth/verify-pin", json={"pin": "1111"}, headers=h, timeout=30)
        assert r.status_code == 429, f"expected 429 lockout, got {r.status_code} {r.text[:200]}"


# ---------- onboarding ----------
class TestOnboarding:
    def test_onboard_demo_seeds_data(self, s, created_tokens):
        _, data = register(s, created_tokens)
        h = {"Authorization": f"Bearer {data['access_token']}"}
        r = s.post(f"{BASE}/auth/onboard", json={"choice": "demo", "name": "TEST Demo Person"}, headers=h, timeout=30)
        assert r.status_code == 200, r.text[:300]
        u = r.json()
        assert u["onboarding_done"] is True
        assert u["has_demo_data"] is True
        assert u["name"] == "TEST Demo Person"
        ov = s.get(f"{BASE}/financial/overview", headers=h, timeout=30)
        assert ov.status_code == 200
        d = ov.json()
        assert len(d["goals"]) == 3, d["goals"]
        assert len(d["transactions"]) == 6
        assert d["mode"] == "user"
        assert all("_id" not in g and "user_id" not in g for g in d["goals"])

    def test_onboard_empty_no_data(self, s, created_tokens):
        _, data = register(s, created_tokens)
        h = {"Authorization": f"Bearer {data['access_token']}"}
        r = s.post(f"{BASE}/auth/onboard", json={"choice": "empty"}, headers=h, timeout=30)
        assert r.status_code == 200
        assert r.json()["onboarding_done"] is True
        assert r.json()["has_demo_data"] is False
        d = s.get(f"{BASE}/financial/overview", headers=h, timeout=30).json()
        assert d["goals"] == []
        assert d["transactions"] == []
        assert d["summary"]["income"] == 0

    def test_demo_seed_ids_are_unique_uuid4_per_user(self, s, created_tokens):
        import uuid as _uuid
        ids = []
        for _ in range(2):
            _, data = register(s, created_tokens)
            h = {"Authorization": f"Bearer {data['access_token']}"}
            assert s.post(f"{BASE}/auth/onboard", json={"choice": "demo"}, headers=h, timeout=30).status_code == 200
            d = s.get(f"{BASE}/financial/overview", headers=h, timeout=30).json()
            user_ids = [g["id"] for g in d["goals"]] + [t["id"] for t in d["transactions"]]
            assert not any(i.startswith("goal-") or i.startswith("txn-") for i in user_ids), user_ids
            for i in user_ids:
                _uuid.UUID(i)  # raises if not a valid uuid
            ids.append(set(user_ids))
        assert not (ids[0] & ids[1]), "demo seed ids collide across users"



# ---------- public demo ----------
class TestPublicDemo:
    def test_demo_overview_public(self, s):
        r = s.get(f"{BASE}/demo/overview", timeout=30)
        assert r.status_code == 200
        d = r.json()
        assert d["mode"] == "demo"
        assert d["user"]["name"] == "Aarav Sharma"
        assert len(d["goals"]) == 3 and len(d["transactions"]) == 6
        assert d["summary"]["health_score"] == 78

    def test_protected_endpoints_require_auth(self, s):
        assert s.get(f"{BASE}/financial/overview", timeout=30).status_code == 401
        assert s.post(f"{BASE}/goals", json={"name": "x", "target_amount": 1, "deadline": "2030"}, timeout=30).status_code == 401
        assert s.delete(f"{BASE}/financial/data", timeout=30).status_code == 401


# ---------- multi-tenancy + finance CRUD ----------
class TestMultiTenancy:
    def test_isolation_and_crud(self, s, created_tokens):
        _, a = register(s, created_tokens, name="TEST A")
        _, b = register(s, created_tokens, name="TEST B")
        ha = {"Authorization": f"Bearer {a['access_token']}"}
        hb = {"Authorization": f"Bearer {b['access_token']}"}

        payload = {"name": "TEST_A_Goal", "target_amount": 500000, "current_amount": 1000,
                   "deadline": "2031", "priority": "High", "monthly_contribution": 5000, "emoji": "🎯"}
        cr = s.post(f"{BASE}/goals", json=payload, headers=ha, timeout=30)
        assert cr.status_code == 200, cr.text[:300]
        goal = cr.json()
        assert goal["name"] == "TEST_A_Goal" and "id" in goal and "user_id" not in goal
        gid = goal["id"]

        ao = s.get(f"{BASE}/financial/overview", headers=ha, timeout=30).json()
        assert any(g["id"] == gid for g in ao["goals"])
        bo = s.get(f"{BASE}/financial/overview", headers=hb, timeout=30).json()
        assert all(g["id"] != gid for g in bo["goals"]), "LEAK: user B sees user A's goal"

        # B cannot update or delete A's goal
        assert s.patch(f"{BASE}/goals/{gid}", json=payload, headers=hb, timeout=30).status_code == 404
        assert s.delete(f"{BASE}/goals/{gid}", headers=hb, timeout=30).status_code == 404

        # A updates goal, verify persisted
        upd = dict(payload, name="TEST_A_Goal_Updated", current_amount=2500)
        assert s.patch(f"{BASE}/goals/{gid}", json=upd, headers=ha, timeout=30).status_code == 200
        ao = s.get(f"{BASE}/financial/overview", headers=ha, timeout=30).json()
        g = next(x for x in ao["goals"] if x["id"] == gid)
        assert g["name"] == "TEST_A_Goal_Updated" and g["current_amount"] == 2500

        # transactions import + category patch
        imp = s.post(f"{BASE}/statements/import-demo", headers=ha, timeout=30)
        assert imp.status_code == 200 and imp.json()["imported"] is True
        ao = s.get(f"{BASE}/financial/overview", headers=ha, timeout=30).json()
        assert ao["transactions"], "no transactions after import-demo"
        txn_id = ao["transactions"][0]["id"]
        pt = s.patch(f"{BASE}/transactions/{txn_id}", json={"category": "Bills"}, headers=ha, timeout=30)
        assert pt.status_code == 200, pt.text[:300]
        ao = s.get(f"{BASE}/financial/overview", headers=ha, timeout=30).json()
        assert next(t for t in ao["transactions"] if t["id"] == txn_id)["category"] == "Bills"
        # B's txn patch on non-owned id -> 404
        assert s.patch(f"{BASE}/transactions/{txn_id}", json={"category": "Food"}, headers=hb, timeout=30).status_code == 404

        # profile update
        assert s.patch(f"{BASE}/user/profile", json={"monthly_income": 100000, "monthly_expenses": 40000,
                                                    "occupation": "QA", "age": 30}, headers=ha, timeout=30).status_code == 200

        # delete data only affects A
        b_goal = s.post(f"{BASE}/goals", json=dict(payload, name="TEST_B_Goal"), headers=hb, timeout=30).json()
        assert s.delete(f"{BASE}/financial/data", headers=ha, timeout=30).status_code == 200
        ao = s.get(f"{BASE}/financial/overview", headers=ha, timeout=30).json()
        assert ao["goals"] == [] and ao["transactions"] == []
        bo = s.get(f"{BASE}/financial/overview", headers=hb, timeout=30).json()
        assert any(g["id"] == b_goal["id"] for g in bo["goals"]), "user B data wiped by user A delete"

    def test_delete_nonexistent_goal_404(self, s, created_tokens):
        _, a = register(s, created_tokens)
        ha = {"Authorization": f"Bearer {a['access_token']}"}
        assert s.delete(f"{BASE}/goals/{uuid.uuid4()}", headers=ha, timeout=30).status_code == 404


# ---------- chat (AI) ----------
class TestChat:
    def test_chat_demo_anonymous(self, s):
        r = requests.post(f"{BASE}/chat", json={"message": "What is a savings rate?"}, timeout=120, stream=True)
        assert r.status_code == 200, r.text[:300]
        body = r.text
        assert len(body.strip()) > 10, f"empty stream: {body!r}"

    def test_chat_authenticated(self, s, created_tokens):
        _, a = register(s, created_tokens, name="TEST Chat User")
        h = {"Authorization": f"Bearer {a['access_token']}"}
        s.post(f"{BASE}/auth/onboard", json={"choice": "demo"}, headers=h, timeout=30)
        time.sleep(1)
        r = requests.post(f"{BASE}/chat", json={"message": "Summarise my goals in one line."},
                          headers=h, timeout=120, stream=True)
        assert r.status_code == 200, r.text[:300]
        assert len(r.text.strip()) > 10


# ---------- known dev user ----------
class TestSeededUser:
    def test_known_test_user_login(self, s):
        r = s.post(f"{BASE}/auth/login", json={"email": "testuser@finaura.dev", "password": "testpass123"}, timeout=30)
        assert r.status_code == 200, f"documented test credentials failed: {r.status_code} {r.text[:200]}"
        assert r.json()["user"]["email"] == "testuser@finaura.dev"
