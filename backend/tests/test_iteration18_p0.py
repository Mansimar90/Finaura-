"""Iteration 18 — validation of the four user-reported P0 fixes on Finaura AI.

Modules / endpoints covered:
  - POST /api/statements/preview, /parse, /confirm-import   (strict CR/DR classification)
  - GET  /api/financial/overview                            (no cross-source double count, demo purge)
  - GET  /api/statements/verify                             (verified_matches score >= 0.85)
  - DELETE /api/statements/{id}                             (scoped delete + 404 isolation)
  - DELETE /api/financial/data                              (multi-tenant wipe)
  - POST /api/statements/resolve-duplicate                  (422/400/404/200)
  - Regression: /api/whatif/*, /api/goals*, /api/auth/*, /api/settings/*
"""
import os
import sys
import uuid

import pytest
import requests
from dotenv import dotenv_values

sys.path.insert(0, "/app/backend")

frontend_env = dotenv_values("/app/frontend/.env")
base_url = os.environ.get("REACT_APP_BACKEND_URL") or frontend_env.get("REACT_APP_BACKEND_URL")
if not base_url:
    raise RuntimeError("REACT_APP_BACKEND_URL missing")
API = base_url.rstrip("/") + "/api"

PWD = "testpass123"

# Bank statement with an explicit Type (DR/CR) column + no signs on the amount
BANK_TYPE_CSV = (
    "Date,Narration,Amount,Type\n"
    "01/09/2025,SWIGGY ORDER BLR,450,DR\n"
    "02/09/2025,SALARY ACME SEP,80000,CR\n"
    "03/09/2025,BIGBASKET GROCERY,2200,DEBIT\n"
    "04/09/2025,INTEREST CREDIT,320,CREDIT\n"
    "05/09/2025,UNKNOWN VENDOR 4412,1500,DR\n"
)

# Bank statement with the classic Debit / Credit column pair
BANK_DRCR_CSV = (
    "Date,Description,Debit,Credit\n"
    "10/09/2025,UPI/SWIGGY/778899001122/Food,450,\n"
    "11/09/2025,SALARY CREDIT ACME,,80000\n"
    "12/09/2025,ATM WITHDRAWAL BLR,5000,\n"
)

# UPI statement: Amount + Merchant only, no direction column at all
UPI_MERCHANT_CSV = (
    "Date,Description,Amount,Merchant,UPI Ref\n"
    "10/09/2025,Paid to merchant,450,SWIGGY,778899001122\n"
    "13/09/2025,Paid to merchant,2500,AMAZON PAY,778899001123\n"
    "14/09/2025,Mystery Vendor 9911,900,MYSTERY VENDOR 9911,778899001124\n"
    "15/09/2025,Salary from Acme Inc,80000,ACME INC,778899001125\n"
)

# UPI statement with an explicit Sent/Received direction column
UPI_SENT_CSV = (
    "Date,Description,Amount,Type,UPI Ref\n"
    "20/09/2025,Zomato order,600,Sent,999000111222\n"
    "21/09/2025,Rahul settle up,1500,Received,999000111223\n"
)


# ---------------- helpers ----------------
def _new_user(suffix=""):
    email = f"it18_{suffix}{uuid.uuid4().hex[:8]}@qa.finaura.dev"
    s = requests.Session()
    r = s.post(f"{API}/auth/register", json={"email": email, "password": PWD, "name": "IT18"}, timeout=60)
    assert r.status_code == 200, f"register failed {r.status_code}: {r.text[:300]}"
    token = r.json().get("token") or r.json().get("access_token")
    assert token, r.text[:300]
    s.headers.update({"Authorization": f"Bearer {token}"})
    s.email = email
    return s


def _parse(client, csv_text, source, filename):
    import json as _json
    files = {"file": (filename, csv_text.encode(), "text/csv")}
    r = client.post(f"{API}/statements/preview", files=files, data={"source": source}, timeout=60)
    assert r.status_code == 200, f"preview {source} failed {r.status_code}: {r.text[:400]}"
    mapping = r.json().get("guess") or {}
    files = {"file": (filename, csv_text.encode(), "text/csv")}
    r = client.post(f"{API}/statements/parse", files=files,
                    data={"mapping": _json.dumps(mapping), "source": source}, timeout=60)
    assert r.status_code == 200, f"parse {source} failed {r.status_code}: {r.text[:400]}"
    txns = r.json()["transactions"]
    assert txns, f"no transactions parsed for {source}"
    return txns


def _import(client, txns, source, filename):
    r = client.post(f"{API}/statements/confirm-import",
                    json={"transactions": txns, "source": source, "file_name": filename}, timeout=60)
    assert r.status_code == 200, f"confirm-import failed {r.status_code}: {r.text[:400]}"
    return r.json()


def _overview(client):
    r = client.get(f"{API}/financial/overview", timeout=60)
    assert r.status_code == 200, f"overview failed {r.status_code}: {r.text[:300]}"
    return r.json()


def _cleanup(client):
    try:
        client.delete(f"{API}/financial/data", timeout=30)
        client.delete(f"{API}/auth/account", timeout=30)
    except Exception:
        pass


# ================= BUG 1: strict Credit/Debit classification =================
class TestStrictClassification:
    @pytest.fixture(scope="class")
    def client(self):
        c = _new_user("cls")
        yield c
        _cleanup(c)

    def test_bank_type_column_dr_cr(self, client):
        txns = _parse(client, BANK_TYPE_CSV, "bank", "bank_sep_type.csv")
        by = {t["description"].upper(): t for t in txns}
        assert by["SWIGGY ORDER BLR"]["type"] == "Expense", by["SWIGGY ORDER BLR"]
        assert by["BIGBASKET GROCERY"]["type"] == "Expense", by["BIGBASKET GROCERY"]
        assert by["UNKNOWN VENDOR 4412"]["type"] == "Expense", by["UNKNOWN VENDOR 4412"]
        assert by["SALARY ACME SEP"]["type"] == "Income", by["SALARY ACME SEP"]
        assert by["INTEREST CREDIT"]["type"] == "Income", by["INTEREST CREDIT"]
        # persisted through confirm-import
        res = _import(client, txns, "bank", "bank_sep_type.csv")
        assert res["imported"] == 5, res
        rows = {t["description"].upper(): t for t in _overview(client)["transactions"]}
        assert rows["UNKNOWN VENDOR 4412"]["type"] == "Expense"
        assert rows["UNKNOWN VENDOR 4412"]["category"] == "Miscellaneous Debit"
        assert rows["SWIGGY ORDER BLR"]["type"] == "Expense"

    def test_no_mongo_internals_leak(self, client):
        for t in _overview(client)["transactions"]:
            assert "_id" not in t and "user_id" not in t, t


class TestUpiDirection:
    @pytest.fixture(scope="class")
    def client(self):
        c = _new_user("upidir")
        yield c
        _cleanup(c)

    def test_upi_amount_plus_merchant_is_expense(self, client):
        """Positive amount + merchant column on a UPI statement must be Expense, never a guessed Income."""
        txns = _parse(client, UPI_MERCHANT_CSV, "upi", "upi_sep.csv")
        by = {(t.get("merchant") or t["description"]).upper(): t for t in txns}
        assert by["SWIGGY"]["type"] == "Expense", by["SWIGGY"]
        assert by["AMAZON PAY"]["type"] == "Expense", by["AMAZON PAY"]
        assert by["MYSTERY VENDOR 9911"]["type"] == "Expense", by["MYSTERY VENDOR 9911"]
        # true salary inflow still detected as income
        assert by["ACME INC"]["type"] == "Income", by["ACME INC"]

    def test_upi_sent_received_column(self, client):
        txns = _parse(client, UPI_SENT_CSV, "upi", "upi_sent.csv")
        by = {t["description"].upper(): t for t in txns}
        assert by["ZOMATO ORDER"]["type"] == "Expense", by["ZOMATO ORDER"]
        assert by["RAHUL SETTLE UP"]["type"] == "Income", by["RAHUL SETTLE UP"]


# ================= BUG 2: bank + UPI unified ledger dedupe =================
class TestCrossSourceDedupe:
    @pytest.fixture(scope="class")
    def client(self):
        c = _new_user("dedupe")
        bank = _parse(c, BANK_DRCR_CSV, "bank", "bank_sep.csv")
        r1 = _import(c, bank, "bank", "bank_sep.csv")
        assert r1["imported"] == 3, r1
        upi = _parse(c, UPI_MERCHANT_CSV, "upi", "upi_sep.csv")
        r2 = _import(c, upi, "upi", "upi_sep.csv")
        c.bank_res, c.upi_res = r1, r2
        yield c
        _cleanup(c)

    def test_merge_reported_on_import(self, client):
        assert client.upi_res["merged"] == 1, f"expected the SWIGGY/778899001122 pair to merge: {client.upi_res}"

    def test_swiggy_counted_once_in_overview(self, client):
        txns = _overview(client)["transactions"]
        swiggy = [t for t in txns if "SWIGGY" in (t["description"] + " " + str(t.get("merchant") or "")).upper()]
        assert len(swiggy) == 1, f"SWIGGY 450 double counted: {swiggy}"
        assert swiggy[0]["amount"] == 450

    def test_totals_not_double_counted(self, client):
        txns = _overview(client)["transactions"]
        expenses = sum(t["amount"] for t in txns if t["type"] == "Expense")
        # swiggy 450 (once) + atm 5000 + amazon 2500 + mystery 900
        assert expenses == 8850, f"unexpected expense total {expenses}: {[(t['description'], t['amount'], t['type']) for t in txns]}"

    def test_verify_surfaces_verified_match(self, client):
        r = client.get(f"{API}/statements/verify", timeout=40)
        assert r.status_code == 200, r.text[:300]
        data = r.json()
        assert "verified_matches" in data and "counts" in data
        assert data["counts"]["verified"] >= 1, data["counts"]
        top = [m for m in data["verified_matches"] if m["score"] >= 0.85]
        assert top, data["verified_matches"]
        assert "verified_bank_ids" not in data


# ================= BUG 3: demo purge on real upload =================
class TestDemoPurge:
    @pytest.fixture(scope="class")
    def client(self):
        c = _new_user("demo")
        yield c
        _cleanup(c)

    def test_demo_seeded(self, client):
        r = client.post(f"{API}/statements/import-demo", timeout=60)
        assert r.status_code == 200, r.text[:300]
        ov = _overview(client)
        assert len(ov["transactions"]) > 0
        assert ov["has_demo_data"] is True, ov.get("has_demo_data")
        assert ov["has_real_data"] is False, ov.get("has_real_data")

    def test_real_upload_purges_demo(self, client):
        upi = _parse(client, UPI_MERCHANT_CSV, "upi", "upi_sep.csv")
        _import(client, upi, "upi", "upi_sep.csv")
        ov = _overview(client)
        sources = {t.get("source") for t in ov["transactions"]}
        assert "demo" not in sources, f"demo rows survived a real upload: {sources}"
        assert len(ov["transactions"]) == 4, f"expected 4 real rows only, got {len(ov['transactions'])}"
        assert ov["has_demo_data"] is False
        assert ov["has_real_data"] is True

    def test_demo_import_blocked_after_real(self, client):
        r = client.post(f"{API}/statements/import-demo", timeout=40)
        assert r.status_code == 409, f"expected 409, got {r.status_code}: {r.text[:200]}"


# ================= BUG 4: delete endpoints =================
class TestDeleteStatement:
    @pytest.fixture(scope="class")
    def ctx(self):
        a = _new_user("dela")
        b = _new_user("delb")
        bank = _parse(a, BANK_DRCR_CSV, "bank", "bank_sep.csv")
        a_bank = _import(a, bank, "bank", "bank_sep.csv")
        upi = _parse(a, UPI_MERCHANT_CSV, "upi", "upi_sep.csv")
        a_upi = _import(a, upi, "upi", "upi_sep.csv")
        b_bank = _import(b, _parse(b, BANK_TYPE_CSV, "bank", "b.csv"), "bank", "b.csv")
        yield {"a": a, "b": b, "a_bank": a_bank, "a_upi": a_upi, "b_bank": b_bank}
        _cleanup(a)
        _cleanup(b)

    def test_delete_nonexistent_404(self, ctx):
        r = ctx["a"].delete(f"{API}/statements/{uuid.uuid4()}", timeout=30)
        assert r.status_code == 404, f"{r.status_code}: {r.text[:200]}"

    def test_delete_foreign_statement_404(self, ctx):
        foreign = ctx["b_bank"]["statement_id"]
        r = ctx["a"].delete(f"{API}/statements/{foreign}", timeout=30)
        assert r.status_code == 404, f"cross-tenant leak! {r.status_code}: {r.text[:200]}"
        # victim data intact
        assert len(_overview(ctx["b"])["transactions"]) == 5

    def test_delete_own_statement_scoped(self, ctx):
        sid = ctx["a_upi"]["statement_id"]
        r = ctx["a"].delete(f"{API}/statements/{sid}", timeout=30)
        assert r.status_code == 200, r.text[:300]
        assert r.json()["deleted"] == 4, r.json()
        remaining = _overview(ctx["a"])["transactions"]
        assert len(remaining) == 3, remaining
        swiggy = [t for t in remaining if "SWIGGY" in t["description"].upper()]
        assert len(swiggy) == 1 and not swiggy[0].get("linked_txn_id"), swiggy
        lst = ctx["a"].get(f"{API}/statements/list", timeout=30).json()
        assert len(lst) == 1 and lst[0]["source"] == "bank", lst

    def test_unauthenticated_delete_rejected(self, ctx):
        r = requests.delete(f"{API}/statements/{ctx['a_bank']['statement_id']}", timeout=30)
        assert r.status_code in (401, 403), r.status_code


class TestDeleteAllData:
    def test_wipes_only_caller_data(self):
        a = _new_user("wipea")
        b = _new_user("wipeb")
        try:
            _import(a, _parse(a, BANK_TYPE_CSV, "bank", "a.csv"), "bank", "a.csv")
            _import(b, _parse(b, BANK_TYPE_CSV, "bank", "b.csv"), "bank", "b.csv")
            ga = a.post(f"{API}/goals", json={"name": "TEST_Goal A", "target_amount": 100000,
                                              "current_amount": 1000, "deadline": "Dec 2026"}, timeout=30)
            assert ga.status_code in (200, 201), ga.text[:200]
            r = a.delete(f"{API}/financial/data", timeout=40)
            assert r.status_code == 200, r.text[:200]
            ov_a = _overview(a)
            assert ov_a["transactions"] == [], ov_a["transactions"]
            assert ov_a["has_real_data"] is False
            assert a.get(f"{API}/statements/list", timeout=30).json() == []
            assert not [g for g in ov_a.get("goals", []) if g.get("name") == "TEST_Goal A"], ov_a.get("goals")
            # tenant B untouched
            assert len(_overview(b)["transactions"]) == 5, "cross-tenant wipe!"
        finally:
            _cleanup(a)
            _cleanup(b)


class TestResolveDuplicate:
    @pytest.fixture(scope="class")
    def ctx(self):
        a = _new_user("resa")
        b = _new_user("resb")
        _import(a, _parse(a, BANK_TYPE_CSV, "bank", "a.csv"), "bank", "a.csv")
        _import(b, _parse(b, BANK_TYPE_CSV, "bank", "b.csv"), "bank", "b.csv")
        a_ids = [t["id"] for t in _overview(a)["transactions"]]
        b_ids = [t["id"] for t in _overview(b)["transactions"]]
        yield {"a": a, "b": b, "a_ids": a_ids, "b_ids": b_ids}
        _cleanup(a)
        _cleanup(b)

    def test_missing_fields_422(self, ctx):
        r = ctx["a"].post(f"{API}/statements/resolve-duplicate", json={"keep_id": ctx["a_ids"][0]}, timeout=30)
        assert r.status_code == 422, f"{r.status_code}: {r.text[:200]}"

    def test_same_id_400(self, ctx):
        i = ctx["a_ids"][0]
        r = ctx["a"].post(f"{API}/statements/resolve-duplicate", json={"keep_id": i, "drop_id": i}, timeout=30)
        assert r.status_code == 400, f"{r.status_code}: {r.text[:200]}"

    def test_foreign_id_404(self, ctx):
        r = ctx["a"].post(f"{API}/statements/resolve-duplicate",
                          json={"keep_id": ctx["a_ids"][0], "drop_id": ctx["b_ids"][0]}, timeout=30)
        assert r.status_code == 404, f"cross-tenant leak! {r.status_code}: {r.text[:200]}"
        assert len(_overview(ctx["b"])["transactions"]) == 5

    def test_valid_resolve(self, ctx):
        keep, drop = ctx["a_ids"][0], ctx["a_ids"][1]
        before = len(_overview(ctx["a"])["transactions"])
        r = ctx["a"].post(f"{API}/statements/resolve-duplicate",
                          json={"keep_id": keep, "drop_id": drop}, timeout=30)
        assert r.status_code == 200, r.text[:300]
        assert r.json()["deleted"] == 1, r.json()
        after = _overview(ctx["a"])["transactions"]
        assert len(after) == before - 1
        assert drop not in [t["id"] for t in after]


# ================= Regressions =================
class TestWhatIfRegression:
    @pytest.fixture(scope="class")
    def client(self):
        c = _new_user("wi")
        c.patch(f"{API}/user/profile", json={"profile": {
            "monthly_income": 120000, "monthly_expenses": 60000,
            "current_savings": 300000, "investments": 200000, "emi": 0}}, timeout=30)
        yield c
        _cleanup(c)

    def test_scenario_four_options(self, client):
        r = client.post(f"{API}/whatif/scenario",
                        json={"item_name": "iPhone 17", "amount": 120000}, timeout=120)
        assert r.status_code == 200, r.text[:400]
        data = r.json()
        opts = data["options"]
        assert len(opts) == 4, [o.get("id") for o in opts]
        assert [o["id"] for o in opts] == ["buy_now", "after_3m", "after_6m", "best"], [o["id"] for o in opts]
        labels = " ".join(o["label"].lower() for o in opts)
        for want in ("buy now", "3 month", "6 month", "ai best"):
            assert want in labels, labels
        assert "user_snapshot" in data and "disclaimer" in data

    def test_subscription(self, client):
        r = client.post(f"{API}/whatif/subscription",
                        json={"item_name": "Netflix", "monthly_cost": 649, "onetime_cost": 20000}, timeout=60)
        assert r.status_code == 200, r.text[:400]
        d = r.json()
        assert d["breakeven_months"] == 31, d["breakeven_months"]
        assert d["subscription"]["total_paid_5y"] == 649 * 60
        assert d["subscription"]["total_paid_10y"] == 649 * 120
        assert d["onetime"]["future_value_5y"] > 20000
        assert d["onetime"]["future_value_10y"] > d["onetime"]["future_value_5y"]

    def test_twin_projection(self, client):
        r = client.post(f"{API}/whatif/twin",
                        json={"monthly_savings": 30000, "annual_return": 10.0, "lump_sum": 100000}, timeout=60)
        assert r.status_code == 200, r.text[:400]
        d = r.json()
        base = d["baseline"]
        assert base["series"], base
        assert [p["month"] for p in base["series"]] == [12, 24, 36, 48, 60, 84, 120], base["series"]
        assert base["final_10y"] > base["final_5y"] > 0
        ids = [s["id"] for s in d["scenarios"]]
        assert "boost_25" in ids and "extra_income" in ids and "lump_sum" in ids, ids
        assert d["user_snapshot"]["monthly_savings"] == 30000


class TestGoalsRegression:
    @pytest.fixture(scope="class")
    def client(self):
        c = _new_user("goal")
        yield c
        _cleanup(c)

    def _goals(self, client):
        return _overview(client)["goals"]

    def test_goal_crud_and_reorder(self, client):
        ids = []
        for name in ("TEST_G1", "TEST_G2"):
            r = client.post(f"{API}/goals", json={
                "name": name, "target_amount": 50000, "current_amount": 5000,
                "deadline": "Dec 2026", "priority": "High", "monthly_contribution": 2000}, timeout=30)
            assert r.status_code in (200, 201), r.text[:300]
            body = r.json()
            assert body["name"] == name and body["target_amount"] == 50000, body
            assert "_id" not in body, body
            ids.append(body["id"])

        # partial PATCH
        r = client.patch(f"{API}/goals/{ids[0]}", json={"current_amount": 9000}, timeout=30)
        assert r.status_code == 200, r.text[:300]
        rows = self._goals(client)
        g0 = next(g for g in rows if g["id"] == ids[0])
        assert g0["current_amount"] == 9000 and g0["name"] == "TEST_G1", g0
        assert g0["target_amount"] == 50000, g0

        # PATCH with no fields -> 400 ; unknown id -> 404
        assert client.patch(f"{API}/goals/{ids[0]}", json={}, timeout=30).status_code == 400
        assert client.patch(f"{API}/goals/{uuid.uuid4()}", json={"current_amount": 1}, timeout=30).status_code == 404

        # reorder
        r = client.post(f"{API}/goals/reorder", json={"ordered_ids": list(reversed(ids))}, timeout=30)
        assert r.status_code == 200, f"{r.status_code}: {r.text[:300]}"
        rows = self._goals(client)
        assert [g["id"] for g in rows][:2] == list(reversed(ids)), [(g["name"], g.get("order")) for g in rows]
        # foreign id in reorder -> 404
        bad = client.post(f"{API}/goals/reorder", json={"ordered_ids": [str(uuid.uuid4())]}, timeout=30)
        assert bad.status_code == 404, bad.status_code

        # delete
        for gid in ids:
            assert client.delete(f"{API}/goals/{gid}", timeout=30).status_code == 200
        assert client.delete(f"{API}/goals/{ids[0]}", timeout=30).status_code == 404


class TestAuthAndSettingsRegression:
    def test_auth_config(self):
        r = requests.get(f"{API}/auth/config", timeout=30)
        assert r.status_code == 200, r.text[:200]
        for key in ("google_enabled", "apple_enabled"):
            assert key in r.json(), r.json()

    def test_register_login_me_logout(self):
        email = f"it18_auth{uuid.uuid4().hex[:8]}@qa.finaura.dev"
        s = requests.Session()
        r = s.post(f"{API}/auth/register", json={"email": email, "password": PWD, "name": "IT18"}, timeout=60)
        assert r.status_code == 200, r.text[:300]
        s2 = requests.Session()
        r = s2.post(f"{API}/auth/login", json={"email": email, "password": PWD}, timeout=40)
        assert r.status_code == 200, r.text[:300]
        token = r.json().get("token") or r.json().get("access_token")
        s2.headers.update({"Authorization": f"Bearer {token}"})
        me = s2.get(f"{API}/auth/me", timeout=30)
        assert me.status_code == 200 and me.json()["email"] == email, me.text[:200]
        bad = requests.post(f"{API}/auth/login", json={"email": email, "password": "wrongpass"}, timeout=30)
        assert bad.status_code in (400, 401), bad.status_code
        noauth = requests.get(f"{API}/financial/overview", timeout=30)
        assert noauth.status_code in (401, 403), noauth.status_code
        out = s2.post(f"{API}/auth/logout", timeout=30)
        assert out.status_code == 200, out.status_code
        try:
            s2.delete(f"{API}/auth/account", timeout=30)
        except Exception:
            pass

    def test_existing_seeded_user_login(self):
        r = requests.post(f"{API}/auth/login",
                          json={"email": "testuser@finaura.dev", "password": "testpass123"}, timeout=40)
        assert r.status_code == 200, f"seeded test user login broken: {r.status_code} {r.text[:200]}"

    def test_preferences_and_export(self):
        c = _new_user("prefs")
        try:
            r = c.get(f"{API}/settings/preferences", timeout=30)
            assert r.status_code == 200, r.text[:300]
            base = r.json()
            assert isinstance(base, dict) and base, base
            key = next(iter(base))
            patch_body = {"theme": "dark"} if "theme" in base else {key: base[key]}
            p = c.patch(f"{API}/settings/preferences", json=patch_body, timeout=30)
            assert p.status_code == 200, p.text[:300]
            after = c.get(f"{API}/settings/preferences", timeout=30).json()
            for k, v in patch_body.items():
                assert after.get(k) == v, (k, after)

            e = c.get(f"{API}/settings/export", timeout=40)
            assert e.status_code == 200, e.text[:300]
            data = e.json()
            assert data["user"]["email"] == c.email, data["user"]
            for k in ("goals", "transactions", "memories"):
                assert k in data, list(data.keys())
            assert "_id" not in str(data), "mongo _id leaked in export"
        finally:
            _cleanup(c)


class TestDeleteDataMemories:
    """DELETE /api/financial/data should also clear AI memories (requirement: wipe ALL user data)."""

    def test_memories_cleared(self):
        c = _new_user("mem")
        try:
            r = c.post(f"{API}/memories", json={"category": "income", "key": "TEST_key",
                                                "value": "TEST_ memory for wipe check"}, timeout=30)
            assert r.status_code in (200, 201), r.text[:300]
            before = c.get(f"{API}/memories", timeout=30).json()["memories"]
            assert before, before
            assert c.delete(f"{API}/financial/data", timeout=40).status_code == 200
            after = c.get(f"{API}/memories", timeout=30).json()
            rows = after if isinstance(after, list) else after.get("memories", [])
            assert rows == [], f"memories survived DELETE /financial/data: {rows}"
        finally:
            _cleanup(c)


# ================= Edge case: bank CSV with a single unsigned Amount column =================
BANK_AMOUNT_ONLY_CSV = (
    "Date,Description,Amount\n"
    "01/09/2025,SWIGGY ORDER BLR,450\n"
    "02/09/2025,BIGBASKET GROCERY,2200\n"
    "03/09/2025,SALARY ACME SEP,80000\n"
)


class TestBankAmountOnlyClassification:
    """Very common Indian bank export: one unsigned 'Amount' column, no Debit/Credit and no
    Type column. Merchant debits must not be guessed as Income."""

    def test_amount_only_bank_debits_not_income(self):
        c = _new_user("amtonly")
        try:
            txns = _parse(c, BANK_AMOUNT_ONLY_CSV, "bank", "bank_amount_only.csv")
            by = {t["description"].upper(): t for t in txns}
            assert by["SWIGGY ORDER BLR"]["type"] == "Expense", by["SWIGGY ORDER BLR"]
            assert by["BIGBASKET GROCERY"]["type"] == "Expense", by["BIGBASKET GROCERY"]
            assert by["SALARY ACME SEP"]["type"] == "Income", by["SALARY ACME SEP"]
        finally:
            _cleanup(c)
