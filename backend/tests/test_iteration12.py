"""Iteration 12 — Goals CRUD+reorder, What-If purchase scenario, Settings preferences/export."""
import os
import re
import uuid
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


def _creds():
    content = Path("/app/memory/test_credentials.md").read_text(encoding="utf-8")
    e = re.search(r'(?im)Email:\s*`([^`]+)`', content)
    p = re.search(r'(?im)Password:\s*`([^`]+)`', content)
    return {"email": e.group(1), "password": p.group(1)}


@pytest.fixture(scope="session")
def s():
    return requests.Session()


@pytest.fixture(scope="session")
def token(s):
    c = _creds()
    r = s.post(f"{API}/auth/login", json=c, timeout=30)
    if r.status_code != 200:
        pytest.fail(f"login failed {r.status_code}: {r.text[:300]}")
    t = r.json().get("token") or r.json().get("access_token")
    assert t, r.json()
    return t


@pytest.fixture(scope="session")
def H(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="session")
def other(s):
    """A second freshly-registered user for isolation tests."""
    email = f"TEST_iso_{uuid.uuid4().hex[:8]}@qa.finaura.dev"
    r = s.post(f"{API}/auth/register", json={"email": email, "password": "testpass123", "name": "TEST Iso"}, timeout=30)
    if r.status_code not in (200, 201):
        pytest.fail(f"register failed {r.status_code}: {r.text[:300]}")
    t = r.json().get("token") or r.json().get("access_token")
    return {"email": email, "headers": {"Authorization": f"Bearer {t}"}}


# ---------------- regression: auth + overview ----------------

class TestRegressionAuth:
    def test_login_and_me(self, s, H):
        r = s.get(f"{API}/auth/me", headers=H, timeout=30)
        assert r.status_code == 200
        assert r.json().get("email") == _creds()["email"]

    def test_auth_config(self, s):
        r = s.get(f"{API}/auth/config", timeout=30)
        assert r.status_code == 200
        d = r.json()
        assert d["google_enabled"] is True
        assert "google_authcode_enabled" in d

    def test_google_start(self, s):
        r = s.get(f"{API}/auth/google/start", timeout=30, allow_redirects=False)
        assert r.status_code in (200, 302, 307)

    def test_google_legacy_bad_token(self, s):
        r = s.post(f"{API}/auth/google", json={"credential": "bogus"}, timeout=30)
        assert r.status_code in (400, 401, 422)

    def test_overview(self, s, H):
        r = s.get(f"{API}/financial/overview", headers=H, timeout=30)
        assert r.status_code == 200
        d = r.json()
        for k in ("summary", "goals", "transactions", "history", "spending", "user"):
            assert k in d
        assert "_id" not in str(d)

    def test_overview_unauth(self, s):
        r = s.get(f"{API}/financial/overview", timeout=30)
        assert r.status_code in (401, 403)


# ---------------- Goals CRUD + reorder ----------------

@pytest.fixture(scope="class")
def created(s, H):
    ids = []
    yield ids
    for gid in ids:
        s.delete(f"{API}/goals/{gid}", headers=H, timeout=30)


class TestGoals:
    def _mk(self, s, H, created, name, **kw):
        payload = {"name": name, "target_amount": 100000, "current_amount": 1000,
                   "deadline": "2030", "priority": "Medium", "monthly_contribution": 5000}
        payload.update(kw)
        r = s.post(f"{API}/goals", json=payload, headers=H, timeout=30)
        assert r.status_code in (200, 201), r.text[:300]
        d = r.json()
        created.append(d["id"])
        return d

    def test_create_assigns_order_and_persists(self, s, H, created):
        before = s.get(f"{API}/financial/overview", headers=H, timeout=30).json()["goals"]
        g1 = self._mk(s, H, created, "TEST_G1")
        assert isinstance(g1["order"], int)
        assert g1["order"] == len(before)
        assert "_id" not in g1 and "user_id" not in g1
        g2 = self._mk(s, H, created, "TEST_G2")
        assert g2["order"] == g1["order"] + 1
        # verify persisted via overview
        goals = s.get(f"{API}/financial/overview", headers=H, timeout=30).json()["goals"]
        names = [g["name"] for g in goals]
        assert "TEST_G1" in names and "TEST_G2" in names
        fetched = next(g for g in goals if g["name"] == "TEST_G1")
        assert fetched["target_amount"] == 100000
        assert fetched["monthly_contribution"] == 5000

    def test_create_validation(self, s, H):
        r = s.post(f"{API}/goals", json={"name": "TEST_bad"}, headers=H, timeout=30)
        assert r.status_code == 422

    def test_update_goal(self, s, H, created):
        g = self._mk(s, H, created, "TEST_upd")
        r = s.patch(f"{API}/goals/{g['id']}", headers=H, timeout=30, json={
            "name": "TEST_upd2", "target_amount": 222222, "current_amount": 5000,
            "deadline": "2031", "priority": "High", "monthly_contribution": 7000})
        assert r.status_code == 200
        goals = s.get(f"{API}/financial/overview", headers=H, timeout=30).json()["goals"]
        f = next((x for x in goals if x["id"] == g["id"]), None)
        assert f is not None
        assert f["name"] == "TEST_upd2"
        assert f["target_amount"] == 222222
        assert f["priority"] == "High"

    def test_update_cross_user_404(self, s, H, other, created):
        g = self._mk(s, H, created, "TEST_xuser")
        r = s.patch(f"{API}/goals/{g['id']}", headers=other["headers"], timeout=30, json={
            "name": "HACKED", "target_amount": 1, "deadline": "2030"})
        assert r.status_code == 404
        goals = s.get(f"{API}/financial/overview", headers=H, timeout=30).json()["goals"]
        assert next(x for x in goals if x["id"] == g["id"])["name"] == "TEST_xuser"

    def test_update_unknown_404(self, s, H):
        r = s.patch(f"{API}/goals/does-not-exist", headers=H, timeout=30, json={
            "name": "x", "target_amount": 1, "deadline": "2030"})
        assert r.status_code == 404

    def test_reorder_persists(self, s, H, created):
        a = self._mk(s, H, created, "TEST_R1")
        b = self._mk(s, H, created, "TEST_R2")
        c = self._mk(s, H, created, "TEST_R3")
        order = [c["id"], a["id"], b["id"]]
        # include remaining goals so full ordering deterministic
        all_goals = s.get(f"{API}/financial/overview", headers=H, timeout=30).json()["goals"]
        rest = [g["id"] for g in all_goals if g["id"] not in order]
        full = order + rest
        r = s.post(f"{API}/goals/reorder", headers=H, timeout=30, json={"ordered_ids": full})
        assert r.status_code == 200, r.text[:300]
        assert r.json()["reordered"] == len(full)
        goals = s.get(f"{API}/financial/overview", headers=H, timeout=30).json()["goals"]
        assert [g["id"] for g in goals] == full

    def test_reorder_unknown_id_404(self, s, H, created):
        g = self._mk(s, H, created, "TEST_R4")
        r = s.post(f"{API}/goals/reorder", headers=H, timeout=30,
                   json={"ordered_ids": [g["id"], "nope-" + uuid.uuid4().hex]})
        assert r.status_code == 404

    def test_reorder_other_user_id_404(self, s, H, other, created):
        g = self._mk(s, H, created, "TEST_R5")
        r = s.post(f"{API}/goals/reorder", headers=other["headers"], timeout=30,
                   json={"ordered_ids": [g["id"]]})
        assert r.status_code == 404

    def test_reorder_empty_list(self, s, H):
        r = s.post(f"{API}/goals/reorder", headers=H, timeout=30, json={"ordered_ids": []})
        assert r.status_code == 200
        assert r.json()["reordered"] == 0

    def test_delete_cross_user_then_own(self, s, H, other):
        r = s.post(f"{API}/goals", headers=H, timeout=30, json={
            "name": "TEST_del", "target_amount": 1000, "deadline": "2030"})
        gid = r.json()["id"]
        assert s.delete(f"{API}/goals/{gid}", headers=other["headers"], timeout=30).status_code == 404
        assert s.delete(f"{API}/goals/{gid}", headers=H, timeout=30).status_code == 200
        assert s.delete(f"{API}/goals/{gid}", headers=H, timeout=30).status_code == 404
        goals = s.get(f"{API}/financial/overview", headers=H, timeout=30).json()["goals"]
        assert gid not in [g["id"] for g in goals]

    def test_goals_unauth(self, s):
        assert s.post(f"{API}/goals", json={"name": "x", "target_amount": 1, "deadline": "2030"},
                      timeout=30).status_code in (401, 403)

    def test_empty_goals_user_overview(self, s, other):
        r = s.get(f"{API}/financial/overview", headers=other["headers"], timeout=30)
        assert r.status_code == 200
        assert r.json()["goals"] == []


# ---------------- Settings preferences ----------------

class TestPreferences:
    def test_defaults_for_new_user(self, s, other):
        r = s.get(f"{API}/settings/preferences", headers=other["headers"], timeout=30)
        assert r.status_code == 200
        d = r.json()
        assert d["currency"] == "INR"
        assert d["theme"] == "system"
        assert d["notifications"]["goal_reminders"] is True
        assert d["notifications"]["weekly_digest"] is False

    def test_partial_update_currency(self, s, other):
        h = other["headers"]
        r = s.patch(f"{API}/settings/preferences", headers=h, timeout=30, json={"currency": "USD"})
        assert r.status_code == 200
        assert r.json()["currency"] == "USD"
        g = s.get(f"{API}/settings/preferences", headers=h, timeout=30).json()
        assert g["currency"] == "USD"
        assert g["date_format"] == "DD-MM-YYYY"  # untouched

    def test_deep_merge_notifications(self, s, other):
        h = other["headers"]
        r = s.patch(f"{API}/settings/preferences", headers=h, timeout=30,
                    json={"notifications": {"weekly_digest": True}})
        assert r.status_code == 200
        n = r.json()["notifications"]
        assert n["weekly_digest"] is True
        assert n["goal_reminders"] is True  # not wiped
        n2 = s.get(f"{API}/settings/preferences", headers=h, timeout=30).json()["notifications"]
        assert n2["weekly_digest"] is True and n2["budget_alerts"] is True

    def test_bad_theme_coerced(self, s, other):
        h = other["headers"]
        r = s.patch(f"{API}/settings/preferences", headers=h, timeout=30, json={"theme": "neon-pink"})
        assert r.status_code == 200
        assert r.json()["theme"] == "system"

    def test_bad_priority_coerced(self, s, other):
        h = other["headers"]
        r = s.patch(f"{API}/settings/preferences", headers=h, timeout=30,
                    json={"goal_default_priority": "Urgent"})
        assert r.json()["goal_default_priority"] == "Medium"

    def test_valid_theme_persists(self, s, other):
        h = other["headers"]
        s.patch(f"{API}/settings/preferences", headers=h, timeout=30, json={"theme": "dark"})
        assert s.get(f"{API}/settings/preferences", headers=h, timeout=30).json()["theme"] == "dark"

    def test_unauth(self, s):
        assert s.get(f"{API}/settings/preferences", timeout=30).status_code in (401, 403)


class TestExport:
    def test_export_shape_and_isolation(self, s, H, other):
        r = s.get(f"{API}/settings/export", headers=H, timeout=60)
        assert r.status_code == 200
        d = r.json()
        for k in ("exported_at", "user", "goals", "transactions", "memories"):
            assert k in d, k
        assert d["user"]["email"] == _creds()["email"]
        assert "preferences" in d["user"]
        assert "_id" not in str(d)
        # other user's export must not include main user's email
        r2 = s.get(f"{API}/settings/export", headers=other["headers"], timeout=60)
        assert r2.status_code == 200
        d2 = r2.json()
        assert d2["user"]["email"].lower() == other["email"].lower()
        assert d2["goals"] == []
        assert _creds()["email"] not in str(d2)

    def test_export_unauth(self, s):
        assert s.get(f"{API}/settings/export", timeout=30).status_code in (401, 403)


# ---------------- What-If purchase scenario ----------------

@pytest.fixture(scope="class")
def scen_user(s):
    """Isolated user with a profile + one goal, so mutation checks are not
    affected by other test workers touching the shared test account."""
    email = f"TEST_scen_{uuid.uuid4().hex[:8]}@qa.finaura.dev"
    r = s.post(f"{API}/auth/register", json={"email": email, "password": "testpass123", "name": "TEST Scen"}, timeout=30)
    assert r.status_code in (200, 201), r.text[:300]
    h = {"Authorization": f"Bearer {r.json().get('token') or r.json().get('access_token')}"}
    s.patch(f"{API}/user/profile", headers=h, timeout=30, json={
        "monthly_income": 185000, "monthly_expenses": 123000, "current_savings": 500000,
        "investments": 250000, "debt": 120000, "emi": 18000})
    s.post(f"{API}/goals", headers=h, timeout=30, json={
        "name": "TEST_ScenGoal", "target_amount": 1000000, "current_amount": 300000,
        "deadline": "2029", "priority": "High", "monthly_contribution": 25000})
    return h


class TestWhatIfScenario:
    def test_scenario_four_options(self, s, scen_user):
        H = scen_user
        before_goals = s.get(f"{API}/financial/overview", headers=H, timeout=30).json()
        r = s.post(f"{API}/whatif/scenario", headers=H, timeout=180,
                   json={"item_name": "Laptop", "amount": 100000, "category": "Electronics"})
        assert r.status_code == 200, r.text[:500]
        d = r.json()
        ids = [o["id"] for o in d["options"]]
        assert ids == ["buy_now", "after_3m", "after_6m", "best"], ids
        for o in d["options"]:
            for k in ("pros", "cons", "goal_impacts", "remaining_cash_after_purchase",
                      "health_score_delta", "label"):
                assert k in o, f"{o['id']} missing {k}"
            assert isinstance(o["pros"], list) and isinstance(o["cons"], list)
            assert isinstance(o["goal_impacts"], list)
        best = d["options"][-1]
        assert "ai_recommendation" in best and "ai_reasoning" in best
        assert "ai_available" in d
        if d["ai_available"]:
            assert len(best["ai_recommendation"]) > 10, "AI available but empty recommendation"
        assert d["user_snapshot"]["goal_count"] == len(before_goals["goals"])
        assert d["user_snapshot"]["monthly_free_cash"] == 185000 - 123000 - 18000
        # a High-priority goal with monthly contribution must show a goal impact on buy_now
        assert d["options"][0]["goal_impacts"], "buy_now has no goal_impacts despite an active goal"
        assert "disclaimer" in d
        # no mutation
        after_goals = s.get(f"{API}/financial/overview", headers=H, timeout=30).json()
        assert after_goals["goals"] == before_goals["goals"]
        assert after_goals["transactions"] == before_goals["transactions"]

    def test_scenario_validation(self, s, H):
        assert s.post(f"{API}/whatif/scenario", headers=H, timeout=60,
                      json={"item_name": "", "amount": 1000}).status_code == 422
        assert s.post(f"{API}/whatif/scenario", headers=H, timeout=60,
                      json={"item_name": "X", "amount": 0}).status_code == 422
        assert s.post(f"{API}/whatif/scenario", headers=H, timeout=60,
                      json={"item_name": "X", "amount": 10_00_00_001}).status_code == 422

    def test_scenario_new_user_no_profile_no_goals(self, s, other):
        r = s.post(f"{API}/whatif/scenario", headers=other["headers"], timeout=180,
                   json={"item_name": "Phone", "amount": 50000, "recurring_monthly_cost": 500})
        assert r.status_code == 200, r.text[:500]
        d = r.json()
        assert len(d["options"]) == 4
        assert d["user_snapshot"]["monthly_free_cash"] == 0
        for o in d["options"]:
            assert o["goal_impacts"] == []

    def test_scenario_unauth(self, s):
        assert s.post(f"{API}/whatif/scenario", json={"item_name": "X", "amount": 100},
                      timeout=30).status_code in (401, 403)

    def test_apply_pins_memory_without_mutating(self, s, scen_user):
        H = scen_user
        before_ov = s.get(f"{API}/financial/overview", headers=H, timeout=30).json()
        before_mem = s.get(f"{API}/settings/export", headers=H, timeout=60).json()["memories"]
        r = s.post(f"{API}/whatif/scenario/apply", headers=H, timeout=60, json={
            "scenario_name": "TEST_Laptop", "amount": 100000,
            "option_label": "After 3 Months", "summary": "TEST pinned plan"})
        assert r.status_code == 200, r.text[:300]
        d = r.json()
        assert d["pinned"] is True and d["memory_id"]
        after = s.get(f"{API}/settings/export", headers=H, timeout=60).json()
        assert len(after["memories"]) == len(before_mem) + 1
        pinned = next((m for m in after["memories"] if m["id"] == d["memory_id"]), None)
        assert pinned is not None
        assert pinned["source"] == "whatif_simulator"
        assert "TEST_Laptop" in pinned["value"]
        # goals & transactions unchanged
        assert after["goals"] == before_ov["goals"] or len(after["goals"]) == len(before_ov["goals"])
        after_ov = s.get(f"{API}/financial/overview", headers=H, timeout=30).json()
        assert after_ov["goals"] == before_ov["goals"]
        assert after_ov["transactions"] == before_ov["transactions"]

    def test_apply_validation(self, s, H):
        assert s.post(f"{API}/whatif/scenario/apply", headers=H, timeout=30, json={
            "scenario_name": "", "amount": 100, "option_label": "x", "summary": "y"}).status_code == 422
        assert s.post(f"{API}/whatif/scenario/apply", headers=H, timeout=30, json={
            "scenario_name": "x", "amount": 0, "option_label": "x", "summary": "y"}).status_code == 422

    def test_legacy_whatif_still_works(self, s, H):
        r = s.post(f"{API}/whatif", headers=H, timeout=60, json={
            "current_monthly_savings": 20000, "monthly_savings_delta": 5000,
            "goal_target": 1000000, "goal_current": 100000})
        assert r.status_code == 200
        d = r.json()
        assert d["proposed_monthly_savings"] == 25000
        assert d["months_to_goal_proposed"] < d["months_to_goal_current"]
        assert len(d["series"]) > 0


# ---------------- Regression: chat / memories / learn ----------------

class TestRegressionMisc:
    def test_chat_models(self, s):
        r = s.get(f"{API}/chat/models", timeout=30)
        assert r.status_code == 200
        assert {m["id"] for m in r.json()["models"]} == {"openai", "claude"}

    def test_chat_streams(self, s, H):
        r = s.post(f"{API}/chat", headers=H, timeout=180,
                   json={"message": "In one short sentence, what is an emergency fund?",
                         "model": "claude"}, stream=True)
        assert r.status_code == 200, r.text[:300]
        body = r.content.decode("utf-8", "ignore")
        assert len(body.strip()) > 20, f"empty stream: {body[:200]}"

    def test_learn_articles(self, s):
        r = s.get(f"{API}/learn/articles", timeout=30)
        assert r.status_code == 200
        assert len(r.json()["articles"]) >= 6

    def test_learn_daily(self, s):
        r = s.get(f"{API}/learn/daily", timeout=30)
        assert r.status_code == 200
        assert r.json()["text"]

    def test_demo_overview(self, s):
        r = s.get(f"{API}/demo/overview", timeout=30)
        assert r.status_code == 200
        assert r.json()["mode"] == "demo"

    def test_memories_list(self, s, H):
        r = s.get(f"{API}/memories", headers=H, timeout=30)
        assert r.status_code in (200, 404)
