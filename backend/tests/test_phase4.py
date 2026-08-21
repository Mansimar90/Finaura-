"""Phase 4 backend tests: memories, learn, what-if, expanded profile, chat memory retrieval."""
import os
import re
import time
from pathlib import Path

import pytest
import requests
from dotenv import dotenv_values

frontend_env = dotenv_values("/app/frontend/.env")
base_url = os.environ.get("REACT_APP_BACKEND_URL") or frontend_env.get("REACT_APP_BACKEND_URL")
if not base_url:
    raise RuntimeError("REACT_APP_BACKEND_URL missing")
BASE_URL = base_url.rstrip("/")
API = f"{BASE_URL}/api"


@pytest.fixture(scope="session")
def creds():
    content = Path("/app/memory/test_credentials.md").read_text(encoding="utf-8")
    email = re.search(r"Email:\s*`([^`]+)`", content).group(1)
    password = re.search(r"Password:\s*`([^`]+)`", content).group(1)
    return {"email": email, "password": password}


@pytest.fixture(scope="session")
def token(creds):
    r = requests.post(f"{API}/auth/login", json=creds, timeout=30)
    if r.status_code != 200:
        pytest.fail(f"login failed {r.status_code}: {r.text[:300]}")
    return r.json()["access_token"]


@pytest.fixture(scope="session")
def client(token):
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    return s


@pytest.fixture(scope="session")
def second_client():
    """A freshly registered second user for isolation checks."""
    email = f"TEST_phase4_{int(time.time())}@qa.finaura.dev"
    r = requests.post(f"{API}/auth/register", json={
        "email": email, "password": "testpass123", "name": "TEST Phase4 B"}, timeout=30)
    if r.status_code not in (200, 201):
        pytest.fail(f"register failed {r.status_code}: {r.text[:300]}")
    tok = r.json().get("access_token")
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {tok}", "Content-Type": "application/json"})
    yield s
    s.delete(f"{API}/memories", timeout=30)
    s.delete(f"{API}/auth/account", timeout=30)


# ---------------- Memories module ----------------

class TestMemories:
    def test_requires_jwt(self):
        r = requests.get(f"{API}/memories", timeout=30)
        assert r.status_code in (401, 403), r.text[:200]

    def test_create_list_upsert(self, client):
        client.delete(f"{API}/memories", timeout=30)
        payload = {"category": "income", "key": "monthly_income", "value": "₹65,000",
                   "numeric_value": 65000, "unit": "INR"}
        r = client.post(f"{API}/memories", json=payload, timeout=30)
        assert r.status_code == 200, r.text[:300]
        body = r.json()
        assert body["category"] == "income"
        assert body["key"] == "monthly_income"
        assert body["value"] == "₹65,000"
        assert body["numeric_value"] == 65000
        assert body["unit"] == "INR"
        assert "_id" not in body

        lst = client.get(f"{API}/memories", timeout=30).json()["memories"]
        assert len([m for m in lst if m["key"] == "monthly_income"]) == 1

        # Re-post SAME category+key -> upsert, not duplicate
        payload2 = dict(payload, value="₹90,000", numeric_value=90000)
        r2 = client.post(f"{API}/memories", json=payload2, timeout=30)
        assert r2.status_code == 200, r2.text[:300]
        lst2 = client.get(f"{API}/memories", timeout=30).json()["memories"]
        matching = [m for m in lst2 if m["category"] == "income" and m["key"] == "monthly_income"]
        assert len(matching) == 1, f"upsert duplicated: {matching}"
        assert matching[0]["value"] == "₹90,000"
        assert matching[0]["numeric_value"] == 90000

    def test_invalid_category_rejected(self, client):
        r = client.post(f"{API}/memories", json={"category": "banana", "key": "k", "value": "v"}, timeout=30)
        assert r.status_code == 400, r.text[:200]

    def test_patch_and_delete(self, client):
        r = client.post(f"{API}/memories", json={
            "category": "goal", "key": "TEST_goal_note", "value": "Buy bike"}, timeout=30)
        assert r.status_code == 200
        mid = r.json()["id"]

        p = client.patch(f"{API}/memories/{mid}", json={"value": "Buy car", "numeric_value": 800000}, timeout=30)
        assert p.status_code == 200, p.text[:300]
        assert p.json()["value"] == "Buy car"
        assert p.json()["numeric_value"] == 800000

        got = [m for m in client.get(f"{API}/memories", timeout=30).json()["memories"] if m["id"] == mid]
        assert got and got[0]["value"] == "Buy car"

        d = client.delete(f"{API}/memories/{mid}", timeout=30)
        assert d.status_code == 200 and d.json().get("deleted") is True
        assert not [m for m in client.get(f"{API}/memories", timeout=30).json()["memories"] if m["id"] == mid]

        assert client.patch(f"{API}/memories/{mid}", json={"value": "x"}, timeout=30).status_code == 404
        assert client.delete(f"{API}/memories/{mid}", timeout=30).status_code == 404

    def test_two_user_isolation(self, client, second_client):
        client.post(f"{API}/memories", json={
            "category": "preference", "key": "TEST_iso_key", "value": "A-only"}, timeout=30)
        b = second_client.get(f"{API}/memories", timeout=30)
        assert b.status_code == 200
        assert not [m for m in b.json()["memories"] if m["key"] == "TEST_iso_key"]

        second_client.post(f"{API}/memories", json={
            "category": "preference", "key": "TEST_iso_b", "value": "B-only"}, timeout=30)
        a_keys = [m["key"] for m in client.get(f"{API}/memories", timeout=30).json()["memories"]]
        assert "TEST_iso_b" not in a_keys

    def test_clear_all(self, second_client):
        second_client.post(f"{API}/memories", json={"category": "tax", "key": "TEST_c1", "value": "x"}, timeout=30)
        r = second_client.delete(f"{API}/memories", timeout=30)
        assert r.status_code == 200 and r.json()["deleted"] >= 1
        assert second_client.get(f"{API}/memories", timeout=30).json()["memories"] == []


# ---------------- Learn module ----------------

class TestLearn:
    def test_list_articles(self):
        r = requests.get(f"{API}/learn/articles", timeout=30)
        assert r.status_code == 200
        arts = r.json()["articles"]
        assert len(arts) == 6, len(arts)
        for a in arts:
            assert a["summary"]
            assert "body" not in a
            assert a["id"] and a["title"] and a["category"]

    def test_article_detail(self):
        r = requests.get(f"{API}/learn/articles/emergency-funds", timeout=30)
        assert r.status_code == 200
        a = r.json()
        assert a["id"] == "emergency-funds"
        assert isinstance(a["body"], list) and len(a["body"]) >= 3
        assert a["body"][0]["heading"] and a["body"][0]["text"]

    def test_article_404(self):
        assert requests.get(f"{API}/learn/articles/does-not-exist", timeout=30).status_code == 404

    def test_daily_deterministic(self):
        r1 = requests.get(f"{API}/learn/daily", timeout=30)
        assert r1.status_code == 200
        d = r1.json()
        for k in ["date", "kind", "text", "index", "of_total"]:
            assert k in d
        assert 0 <= d["index"] < d["of_total"]
        r2 = requests.get(f"{API}/learn/daily", timeout=30).json()
        assert r2 == d


# ---------------- What-If simulator ----------------

class TestWhatIf:
    PAYLOAD = {"current_monthly_savings": 10000, "monthly_savings_delta": 5000,
               "goal_target": 500000, "goal_current": 100000,
               "expected_annual_return": 10, "years_horizon": 5}

    def test_requires_jwt(self):
        r = requests.post(f"{API}/whatif", json=self.PAYLOAD, timeout=30)
        assert r.status_code in (401, 403)

    def test_projection(self, client):
        r = client.post(f"{API}/whatif", json=self.PAYLOAD, timeout=30)
        assert r.status_code == 200, r.text[:300]
        d = r.json()
        assert d["months_to_goal_current"] and d["months_to_goal_proposed"]
        assert d["months_to_goal_proposed"] < d["months_to_goal_current"]
        assert isinstance(d["series"], list) and len(d["series"]) > 5
        assert set(d["series"][0]) == {"month", "current", "proposed"}
        assert d["series"][-1]["proposed"] > d["series"][-1]["current"]
        assert isinstance(d["disclaimer"], str) and len(d["disclaimer"]) > 20
        assert d["proposed_monthly_savings"] == 15000

    def test_zero_savings_no_goal(self, client):
        r = client.post(f"{API}/whatif", json={
            "current_monthly_savings": 0, "monthly_savings_delta": 0,
            "goal_target": 500000, "goal_current": 0}, timeout=30)
        assert r.status_code == 200
        assert r.json()["months_to_goal_current"] is None

    def test_validation(self, client):
        r = client.post(f"{API}/whatif", json={"current_monthly_savings": -5, "goal_target": 0}, timeout=30)
        assert r.status_code == 422


# ---------------- Expanded profile ----------------

class TestProfile:
    def test_requires_jwt(self):
        assert requests.get(f"{API}/user/profile", timeout=30).status_code in (401, 403)

    def test_patch_persists_new_fields(self, client):
        payload = {
            "name": "Test User P4", "occupation": "Engineer", "phone": "+919812345678",
            "dob": "1996-04-12", "location": "Bengaluru", "financial_experience": "intermediate",
            "risk_tolerance": "balanced", "interests": ["mutual-funds", "tax"],
            "avatar_url": "https://example.com/a.png", "current_savings": 275000,
        }
        r = client.patch(f"{API}/user/profile", json=payload, timeout=30)
        assert r.status_code == 200, r.text[:300]
        g = client.get(f"{API}/user/profile", timeout=30)
        assert g.status_code == 200
        d = g.json()
        for k, v in payload.items():
            if k == "name":
                assert d["name"] == v
            else:
                assert d[k] == v, f"{k}: {d.get(k)} != {v}"
        assert "_id" not in d

    def test_partial_patch_keeps_other_fields(self, client):
        client.patch(f"{API}/user/profile", json={"location": "Pune"}, timeout=30)
        d = client.get(f"{API}/user/profile", timeout=30).json()
        assert d["location"] == "Pune"
        assert d["risk_tolerance"] == "balanced"


# ---------------- Chat: memory retrieval + tax context ----------------

def _chat(token, message, model="openai"):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = requests.post(f"{API}/chat", json={"message": message, "model": model},
                      headers=headers, timeout=120)
    return r


class TestChat:
    def test_models_endpoint(self):
        r = requests.get(f"{API}/chat/models", timeout=30)
        assert r.status_code == 200
        ids = [m["id"] for m in r.json()["models"]]
        assert "openai" in ids and "claude" in ids

    def test_memory_retrieval_in_chat(self, client, token):
        # profile holds an older/other value; memory should be the one surfaced
        client.patch(f"{API}/user/profile", json={"monthly_income": 55000}, timeout=30)
        client.post(f"{API}/memories", json={
            "category": "income", "key": "monthly_income", "value": "₹65,000",
            "numeric_value": 65000, "unit": "INR"}, timeout=30)
        r = _chat(token, "What is my monthly income? Answer with the exact figure on file.")
        assert r.status_code == 200, r.text[:300]
        text = r.text
        assert len(text) > 10, text
        normalized = text.replace(",", "").replace(" ", "")
        assert ("65000" in normalized or "65k" in normalized.lower()), f"memory not reflected: {text[:400]}"

    def test_tax_context_slabs(self, token):
        r = _chat(token, "Explain the FY 2025-26 new tax regime slabs briefly.")
        assert r.status_code == 200
        t = r.text
        assert "2025-26" in t or "2025–26" in t, t[:300]
        norm = t.replace(",", "").replace(" ", "").lower()
        hits = sum(1 for m in ["4l", "400000", "8l", "800000", "12l", "1200000"] if m in norm)
        assert hits >= 2, f"slab boundaries missing: {t[:500]}"

    def test_demo_chat_anonymous(self):
        r = _chat(None, "What is my income?")
        assert r.status_code == 200
        norm = r.text.replace(",", "").replace(" ", "")
        assert "185000" in norm or "1.85" in r.text or "185" in norm, r.text[:400]


# ---------------- Phase 2/3 regression smoke ----------------

class TestRegression:
    def test_demo_overview(self):
        r = requests.get(f"{API}/demo/overview", timeout=30)
        assert r.status_code == 200
        d = r.json()
        assert d["mode"] == "demo" and len(d["history"]) == 6 and len(d["goals"]) == 3

    def test_auth_config(self):
        r = requests.get(f"{API}/auth/config", timeout=30)
        assert r.status_code == 200
        assert set(["google_enabled", "apple_enabled", "resend_enabled"]) <= set(r.json())

    def test_auth_me(self, client):
        r = client.get(f"{API}/auth/me", timeout=30)
        assert r.status_code == 200, r.text[:200]
        assert r.json().get("email")

    def test_goals_crud(self, client):
        c = client.post(f"{API}/goals", json={
            "name": "TEST_goal", "target_amount": 100000, "current_amount": 1000,
            "deadline": "2030", "priority": "Low", "monthly_contribution": 500}, timeout=30)
        assert c.status_code == 200, c.text[:300]
        gid = c.json()["id"]
        assert "_id" not in c.json()

        ov = client.get(f"{API}/financial/overview", timeout=30).json()
        assert any(g["id"] == gid for g in ov["goals"])

        u = client.patch(f"{API}/goals/{gid}", json={
            "name": "TEST_goal", "target_amount": 999999, "current_amount": 1000,
            "deadline": "2030", "priority": "Low", "monthly_contribution": 500}, timeout=30)
        assert u.status_code == 200 and u.json()["target_amount"] == 999999
        ov2 = client.get(f"{API}/financial/overview", timeout=30).json()
        assert [g for g in ov2["goals"] if g["id"] == gid][0]["target_amount"] == 999999

        d = client.delete(f"{API}/goals/{gid}", timeout=30)
        assert d.status_code == 200
        ov3 = client.get(f"{API}/financial/overview", timeout=30).json()
        assert not any(g["id"] == gid for g in ov3["goals"])
        assert client.delete(f"{API}/goals/{gid}", timeout=30).status_code == 404

    def test_passkey_state(self, client):
        r = client.get(f"{API}/auth/passkey/list", timeout=30)
        assert r.status_code == 200, r.text[:200]

    def test_statements_import_demo(self, client):
        r = client.post(f"{API}/statements/import-demo", timeout=30)
        assert r.status_code == 200 and r.json()["imported"] is True
