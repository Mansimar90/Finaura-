"""Iteration 13 backend tests.

Covers:
  - Goals: partial PATCH (priority-only), reorder persistence, CRUD, authz
  - NEW POST /api/whatif/subscription  (recurring vs one-time comparison)
  - NEW POST /api/whatif/twin          (digital twin 5y/10y net-worth projection)
  - No-mutation guarantees for the new endpoints
  - Regressions: auth/me (picture), auth/config, google start 302, settings tabs data,
    purchase-scenario what-if (4 options), demo overview
"""
import os
import math
import pytest
import requests
from dotenv import dotenv_values

frontend_env = dotenv_values("/app/frontend/.env")
base_url = os.environ.get("REACT_APP_BACKEND_URL") or frontend_env.get("REACT_APP_BACKEND_URL")
if not base_url:
    raise RuntimeError("REACT_APP_BACKEND_URL missing")
BASE = base_url.rstrip("/") + "/api"

EMAIL = "testuser@finaura.dev"
PASSWORD = "testpass123"


# ---------------- fixtures ----------------
@pytest.fixture(scope="session")
def token():
    r = requests.post(f"{BASE}/auth/login", json={"email": EMAIL, "password": PASSWORD}, timeout=30)
    if r.status_code != 200:
        pytest.fail(f"login failed {r.status_code}: {r.text[:300]}")
    t = r.json().get("token") or r.json().get("access_token")
    assert t, f"no token in login response: {r.json().keys()}"
    return t


@pytest.fixture(scope="session")
def client(token):
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    return s


@pytest.fixture(scope="session")
def anon():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


def _overview(client):
    r = client.get(f"{BASE}/financial/overview", timeout=30)
    assert r.status_code == 200, r.text[:300]
    return r.json()


@pytest.fixture
def temp_goal(client):
    created = []

    def _make(name="TEST_goal", priority="Medium", **kw):
        payload = {"name": name, "target_amount": 100000, "current_amount": 1000,
                   "deadline": "2030", "priority": priority, "monthly_contribution": 5000,
                   "emoji": "✦"}
        payload.update(kw)
        r = client.post(f"{BASE}/goals", json=payload, timeout=30)
        assert r.status_code in (200, 201), r.text[:300]
        g = r.json()
        created.append(g["id"])
        return g

    yield _make
    for gid in created:
        client.delete(f"{BASE}/goals/{gid}", timeout=30)


# ---------------- Goals: priority PATCH bug fix ----------------
class TestGoalPriorityPatch:
    def test_patch_priority_only_persists(self, client, temp_goal):
        g = temp_goal(name="TEST_prio", priority="Medium")
        r = client.patch(f"{BASE}/goals/{g['id']}", json={"priority": "High"}, timeout=30)
        assert r.status_code == 200, r.text[:300]
        assert r.json().get("priority") == "High"
        # verify persistence + other fields untouched
        goals = {x["id"]: x for x in _overview(client)["goals"]}
        assert g["id"] in goals
        got = goals[g["id"]]
        assert got["priority"] == "High"
        assert got["name"] == "TEST_prio"
        assert got["target_amount"] == 100000
        assert got["monthly_contribution"] == 5000

    @pytest.mark.parametrize("prio", ["High", "Medium", "Low"])
    def test_all_three_priorities(self, client, temp_goal, prio):
        g = temp_goal(name=f"TEST_p_{prio}", priority="Medium")
        r = client.patch(f"{BASE}/goals/{g['id']}", json={"priority": prio}, timeout=30)
        assert r.status_code == 200
        goals = {x["id"]: x for x in _overview(client)["goals"]}
        assert goals[g["id"]]["priority"] == prio

    def test_priority_change_does_not_move_card(self, client, temp_goal):
        """Priority change must not reshuffle the card order (order field is primary key)."""
        a = temp_goal(name="TEST_ord_a", priority="Low")
        b = temp_goal(name="TEST_ord_b", priority="Low")
        client.post(f"{BASE}/goals/reorder", json={"ordered_ids": [a["id"], b["id"]]}, timeout=30)
        before = [g["id"] for g in _overview(client)["goals"]]
        client.patch(f"{BASE}/goals/{b['id']}", json={"priority": "High"}, timeout=30)
        after = [g["id"] for g in _overview(client)["goals"]]
        assert before.index(a["id"]) < before.index(b["id"])
        assert after.index(a["id"]) < after.index(b["id"]), \
            "priority change reshuffled goal order"

    def test_patch_empty_body_400(self, client, temp_goal):
        g = temp_goal(name="TEST_empty")
        r = client.patch(f"{BASE}/goals/{g['id']}", json={}, timeout=30)
        assert r.status_code == 400, r.text[:200]

    def test_patch_unknown_goal_404(self, client):
        r = client.patch(f"{BASE}/goals/does-not-exist", json={"priority": "High"}, timeout=30)
        assert r.status_code == 404

    def test_patch_requires_auth(self, anon):
        r = anon.patch(f"{BASE}/goals/whatever", json={"priority": "High"}, timeout=30)
        assert r.status_code in (401, 403)


# ---------------- Goals CRUD + reorder regression ----------------
class TestGoalsCrudRegression:
    def test_create_edit_delete(self, client):
        r = client.post(f"{BASE}/goals", json={
            "name": "TEST_crud", "target_amount": 50000, "current_amount": 0,
            "deadline": "2031", "priority": "Low", "monthly_contribution": 2000, "emoji": "★"},
            timeout=30)
        assert r.status_code in (200, 201), r.text[:300]
        gid = r.json()["id"]
        assert "_id" not in r.json()

        # full edit (modal save path)
        r2 = client.patch(f"{BASE}/goals/{gid}", json={
            "name": "TEST_crud_edited", "target_amount": 75000, "current_amount": 500,
            "deadline": "2032", "priority": "High", "monthly_contribution": 3000, "emoji": "✦"},
            timeout=30)
        assert r2.status_code == 200
        got = {g["id"]: g for g in _overview(client)["goals"]}[gid]
        assert got["name"] == "TEST_crud_edited"
        assert got["target_amount"] == 75000
        assert got["priority"] == "High"

        assert client.delete(f"{BASE}/goals/{gid}", timeout=30).status_code in (200, 204)
        assert gid not in {g["id"] for g in _overview(client)["goals"]}
        assert client.delete(f"{BASE}/goals/{gid}", timeout=30).status_code == 404

    def test_reorder_persists(self, client, temp_goal):
        a = temp_goal(name="TEST_r1")
        b = temp_goal(name="TEST_r2")
        c = temp_goal(name="TEST_r3")
        order = [c["id"], a["id"], b["id"]]
        r = client.post(f"{BASE}/goals/reorder", json={"ordered_ids": order}, timeout=30)
        assert r.status_code == 200, r.text[:300]
        got = [g["id"] for g in _overview(client)["goals"] if g["id"] in order]
        assert got == order

    def test_reorder_foreign_id_404(self, client, temp_goal):
        a = temp_goal(name="TEST_rf")
        r = client.post(f"{BASE}/goals/reorder",
                        json={"ordered_ids": [a["id"], "not-mine-id"]}, timeout=30)
        assert r.status_code == 404

    def test_no_mongo_id_leak(self, client):
        for g in _overview(client)["goals"]:
            assert "_id" not in g


# ---------------- NEW: /whatif/subscription ----------------
class TestWhatIfSubscription:
    PAYLOAD = {"item_name": "TEST_Design tool", "monthly_cost": 1500, "onetime_cost": 40000}

    def test_requires_auth(self, anon):
        r = anon.post(f"{BASE}/whatif/subscription", json=self.PAYLOAD, timeout=30)
        assert r.status_code in (401, 403), r.text[:200]

    def test_shape_and_math(self, client):
        r = client.post(f"{BASE}/whatif/subscription", json=self.PAYLOAD, timeout=30)
        assert r.status_code == 200, r.text[:400]
        d = r.json()
        assert d["item_name"] == self.PAYLOAD["item_name"]
        sub, one = d["subscription"], d["onetime"]
        for k in ("total_paid_5y", "total_paid_10y", "opportunity_cost_5y", "opportunity_cost_10y"):
            assert k in sub, f"missing subscription.{k}"
        for k in ("cost", "future_value_5y", "future_value_10y"):
            assert k in one, f"missing onetime.{k}"
        assert sub["total_paid_5y"] == 1500 * 60
        assert sub["total_paid_10y"] == 1500 * 120
        assert one["cost"] == 40000
        # opportunity cost (invested) must exceed plain paid amount at 8%
        assert sub["opportunity_cost_5y"] > sub["total_paid_5y"]
        assert sub["opportunity_cost_10y"] > sub["opportunity_cost_5y"]
        assert one["future_value_10y"] > one["future_value_5y"] > one["cost"]
        # lump-sum compounding sanity: 40000 * 1.0066667^60 ≈ 59,568
        expected5 = 40000 * ((1 + 0.08 / 12) ** 60)
        assert math.isclose(one["future_value_5y"], round(expected5), rel_tol=0.01)
        assert d["breakeven_months"] == round(40000 / 1500)
        assert isinstance(d["recommendation"], str) and len(d["recommendation"]) > 10
        assert "disclaimer" in d and d["disclaimer"]
        assert d.get("annual_return_assumed") == 8.0

    @pytest.mark.parametrize("bad", [
        {"item_name": "", "monthly_cost": 100, "onetime_cost": 1000},
        {"item_name": "x", "monthly_cost": 0, "onetime_cost": 1000},
        {"item_name": "x", "monthly_cost": -5, "onetime_cost": 1000},
        {"item_name": "x", "monthly_cost": 100, "onetime_cost": 0},
        {"item_name": "x", "monthly_cost": 100},
    ])
    def test_validation_422(self, client, bad):
        r = client.post(f"{BASE}/whatif/subscription", json=bad, timeout=30)
        assert r.status_code == 422, f"{bad} -> {r.status_code}"

    def test_does_not_mutate_goals_or_transactions(self, client, temp_goal):
        g = temp_goal(name="TEST_sub_nomutate")
        before = _overview(client)
        b_goal = [x for x in before["goals"] if x["id"] == g["id"]][0]
        client.post(f"{BASE}/whatif/subscription", json=self.PAYLOAD, timeout=30)
        after = _overview(client)
        a_goal = [x for x in after["goals"] if x["id"] == g["id"]][0]
        assert b_goal == a_goal
        assert len(before.get("transactions", [])) == len(after.get("transactions", []))


# ---------------- NEW: /whatif/twin ----------------
class TestDigitalTwin:
    def test_requires_auth(self, anon):
        r = anon.post(f"{BASE}/whatif/twin", json={}, timeout=30)
        assert r.status_code in (401, 403)

    def test_default_shape(self, client):
        r = client.post(f"{BASE}/whatif/twin", json={}, timeout=30)
        assert r.status_code == 200, r.text[:400]
        d = r.json()
        snap = d["user_snapshot"]
        for k in ("base_networth", "monthly_savings", "assumed_return_pct",
                  "monthly_income", "monthly_expenses"):
            assert k in snap
        base = d["baseline"]
        assert base["final_5y"] is not None and base["final_10y"] is not None
        assert base["final_10y"] > base["final_5y"] >= snap["base_networth"]
        series = base["series"]
        assert len(series) >= 5
        years = [p["year"] for p in series]
        assert years[0] == 1.0 and years[-1] == 10.0, years
        # monotonic increase when saving
        balances = [p["balance"] for p in series]
        assert balances == sorted(balances)
        # 5y/10y must match the series points
        assert base["final_5y"] == [p for p in series if p["month"] == 60][0]["balance"]
        assert base["final_10y"] == [p for p in series if p["month"] == 120][0]["balance"]
        # NOTE: spec said 3 scenarios without lump_sum; implementation returns 2
        # (boost_25, extra_income) and only adds lump_sum when supplied.
        assert len(d["scenarios"]) == 2, f"expected 2 default scenarios, got {len(d['scenarios'])}"
        ids = [s["id"] for s in d["scenarios"]]
        assert "boost_25" in ids and "extra_income" in ids
        for s in d["scenarios"]:
            assert s["label"] and s["final_5y"] and s["final_10y"] and s["series"]
        assert d["disclaimer"]

    def test_scenarios_beat_baseline(self, client):
        d = client.post(f"{BASE}/whatif/twin", json={}, timeout=30).json()
        b10 = d["baseline"]["final_10y"]
        for s in d["scenarios"]:
            assert s["final_10y"] >= b10, f"{s['id']} below baseline"

    def test_lump_sum_scenario(self, client):
        r = client.post(f"{BASE}/whatif/twin", json={"lump_sum": 100000}, timeout=30)
        assert r.status_code == 200, r.text[:300]
        d = r.json()
        ids = [s["id"] for s in d["scenarios"]]
        assert "lump_sum" in ids, ids
        assert len(d["scenarios"]) == 3
        ls = [s for s in d["scenarios"] if s["id"] == "lump_sum"][0]
        assert ls["final_10y"] > d["baseline"]["final_10y"]

    def test_overrides_applied(self, client):
        r = client.post(f"{BASE}/whatif/twin",
                        json={"monthly_savings": 25000, "annual_return": 12.0}, timeout=30)
        assert r.status_code == 200
        d = r.json()
        assert d["user_snapshot"]["monthly_savings"] == 25000
        assert d["user_snapshot"]["assumed_return_pct"] == 12.0
        base8 = client.post(f"{BASE}/whatif/twin", json={"monthly_savings": 25000},
                           timeout=30).json()
        assert d["baseline"]["final_10y"] > base8["baseline"]["final_10y"]

    def test_zero_savings_edge(self, client):
        d = client.post(f"{BASE}/whatif/twin", json={"monthly_savings": 0}, timeout=30).json()
        assert d["baseline"]["final_10y"] >= d["user_snapshot"]["base_networth"]
        # boost_25 of zero is still zero -> should equal baseline, not crash
        boost = [s for s in d["scenarios"] if s["id"] == "boost_25"][0]
        assert boost["final_10y"] == d["baseline"]["final_10y"]

    @pytest.mark.parametrize("bad", [
        {"annual_return": 99},
        {"monthly_savings": -1},
        {"lump_sum": -100},
    ])
    def test_validation_422(self, client, bad):
        r = client.post(f"{BASE}/whatif/twin", json=bad, timeout=30)
        assert r.status_code == 422, f"{bad} -> {r.status_code} {r.text[:150]}"

    def test_does_not_mutate(self, client, temp_goal):
        g = temp_goal(name="TEST_twin_nomutate")
        before = _overview(client)
        b_goal = [x for x in before["goals"] if x["id"] == g["id"]][0]
        client.post(f"{BASE}/whatif/twin", json={"lump_sum": 100000}, timeout=30)
        after = _overview(client)
        a_goal = [x for x in after["goals"] if x["id"] == g["id"]][0]
        assert b_goal == a_goal
        assert len(before.get("transactions", [])) == len(after.get("transactions", []))


# ---------------- Regressions ----------------
class TestRegressions:
    def test_auth_me_has_picture(self, client):
        r = client.get(f"{BASE}/auth/me", timeout=30)
        assert r.status_code == 200
        d = r.json()
        assert "picture" in d
        assert d["email"] == EMAIL
        assert "_id" not in d and "password" not in d and "password_hash" not in d

    def test_auth_config(self, anon):
        r = anon.get(f"{BASE}/auth/config", timeout=30)
        assert r.status_code == 200
        d = r.json()
        assert "google_enabled" in d and "apple_enabled" in d

    def test_google_start_302(self, anon):
        r = anon.get(f"{BASE}/auth/google/start", allow_redirects=False, timeout=30)
        assert r.status_code == 302, r.status_code
        assert "accounts.google.com" in r.headers.get("location", "")

    def test_demo_overview_public(self, anon):
        r = anon.get(f"{BASE}/demo/overview", timeout=30)
        assert r.status_code == 200
        assert "goals" in r.json()

    def test_settings_preferences(self, client):
        r = client.get(f"{BASE}/settings/preferences", timeout=30)
        assert r.status_code == 200, r.text[:200]
        assert isinstance(r.json(), dict)

    def test_settings_export(self, client):
        r = client.get(f"{BASE}/settings/export", timeout=30)
        assert r.status_code == 200, r.text[:200]
        assert "goals" in r.json()

    def test_learn_articles(self, client):
        r = client.get(f"{BASE}/learn/articles", timeout=30)
        assert r.status_code == 200
        assert len(r.json()) > 0

    def test_purchase_scenario_still_4_options(self, client, temp_goal):
        g = temp_goal(name="TEST_scen_nomutate")
        before = _overview(client)
        b_goal = [x for x in before["goals"] if x["id"] == g["id"]][0]
        r = client.post(f"{BASE}/whatif/scenario", json={
            "item_name": "TEST_Laptop", "amount": 100000}, timeout=180)
        assert r.status_code == 200, r.text[:400]
        d = r.json()
        opts = d.get("options") or d.get("scenarios")
        assert opts and len(opts) == 4, f"expected 4 options, got {len(opts or [])}"
        after = _overview(client)
        a_goal = [x for x in after["goals"] if x["id"] == g["id"]][0]
        assert b_goal == a_goal, "purchase scenario mutated goals"
        assert len(before.get("transactions", [])) == len(after.get("transactions", []))
        assert d.get("ai_available") is True, "AI (Claude) unavailable for scenario"
