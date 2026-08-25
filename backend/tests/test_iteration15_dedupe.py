"""Iteration 15 — cross-source dedupe fix (dedupe_across_sources) + resolve-duplicate hardening.

Modules covered:
  - GET  /api/financial/overview   (shared matcher: ±3 day, amount, type, ref)
  - GET  /api/statements/verify    (no matched_bank_ids leak, no Mongo internals)
  - POST /api/statements/resolve-duplicate (422 / 404 / 400 / 200)
"""
import asyncio
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
import requests
from dotenv import dotenv_values
from motor.motor_asyncio import AsyncIOMotorClient

frontend_env = dotenv_values("/app/frontend/.env")
base_url = os.environ.get("REACT_APP_BACKEND_URL") or frontend_env.get("REACT_APP_BACKEND_URL")
if not base_url:
    raise RuntimeError("REACT_APP_BACKEND_URL missing")
BASE_URL = base_url.rstrip("/")
API = f"{BASE_URL}/api"
BACKEND_ENV = dotenv_values("/app/backend/.env")
TEST_EMAIL = "testuser@finaura.dev"


# ---------------- helpers / fixtures ----------------
@pytest.fixture(scope="session")
def credentials():
    content = Path("/app/memory/test_credentials.md").read_text(encoding="utf-8")
    email = re.search(r"Email:\s*`([^`]+)`", content)
    pwd = re.search(r"Password:\s*`([^`]+)`", content)
    if not email or not pwd:
        pytest.skip("credentials not parsable")
    return {"email": email.group(1), "password": pwd.group(1)}


@pytest.fixture(scope="session")
def client(credentials):
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json=credentials, timeout=30)
    if r.status_code != 200:
        pytest.fail(f"login failed {r.status_code}: {r.text[:300]}")
    token = r.json().get("token") or r.json().get("access_token")
    if not token:
        pytest.fail(f"no token in login response: {r.text[:300]}")
    s.headers.update({"Authorization": f"Bearer {token}"})
    return s


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


async def _db():
    return AsyncIOMotorClient(BACKEND_ENV["MONGO_URL"])[BACKEND_ENV["DB_NAME"]]


async def _uid(db):
    u = await db.users.find_one({"email": TEST_EMAIL})
    return str(u["_id"])


def insert_txns(docs: list[dict]) -> list[str]:
    """Insert transactions directly (no import endpoint needed). Returns ids."""
    async def _go():
        db = await _db()
        uid = await _uid(db)
        rows = []
        for d in docs:
            row = {
                "id": str(uuid.uuid4()),
                "user_id": uid,
                "created_at": datetime.now(timezone.utc),
                "type": "Expense",
                "category": "Other",
                "source": "bank",
                **d,
            }
            rows.append(row)
        await db.finaura_transactions.insert_many(rows)
        return [r["id"] for r in rows]
    return _run(_go())


def purge(prefix="TEST15_"):
    async def _go():
        db = await _db()
        uid = await _uid(db)
        r = await db.finaura_transactions.delete_many(
            {"user_id": uid, "description": {"$regex": prefix, "$options": "i"}})
        return r.deleted_count
    return _run(_go())


@pytest.fixture(autouse=True)
def clean_around():
    """Cross-worker mutex: these tests share one preview backend + one test user and
    assert on hardcoded amounts, so two xdist workers running them concurrently would
    see each other's rows (and each other's purge). Serialize with a file lock."""
    import fcntl
    lock = open("/tmp/finaura_dedupe_tests.lock", "w")
    fcntl.flock(lock, fcntl.LOCK_EX)
    try:
        purge()
        yield
        purge()
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()


def _spend(overview, name):
    for s in overview.get("spending", []):
        if s["name"] == name:
            return s["value"]
    return 0


# ---------------- the HIGH priority regression ----------------
class TestDoubleCount:
    def test_no_double_count_with_settlement_lag(self, client):
        """UPI 12 Aug + bank 11 Aug (ref in narration) -> verified match AND one row only."""
        ref = "998877665544"
        insert_txns([
            {"description": f"UPI/TEST15_Gym Pro/{ref}", "amount": 1507, "date": "21 Aug 2026",
             "source": "bank", "category": "Other"},
            {"description": "TEST15_Gym Pro", "amount": 1507, "date": "22 Aug 2026",
             "source": "upi", "category": "Other", "merchant": "TEST15_Gym Pro", "upi_ref": ref},
        ])
        v = client.get(f"{API}/statements/verify", timeout=60)
        assert v.status_code == 200, v.text[:300]
        vd = v.json()
        match = next((m for m in vd["verified_matches"] if m["upi_txn"]["amount"] == 1507), None)
        assert match, f"1507 pair not in verified_matches: {vd['counts']}"
        assert match["score"] >= 0.85

        ov = client.get(f"{API}/financial/overview", timeout=60)
        assert ov.status_code == 200, ov.text[:300]
        od = ov.json()
        rows = [t for t in od["transactions"] if t["amount"] == 1507.0
                and "gym pro" in t["description"].lower()]
        assert len(rows) == 1, f"double counted: {rows}"
        assert rows[0]["source"] == "upi", rows[0]
        # spending donut: Other must include 1507 exactly once
        insert_ref_other = _spend(od, "Other")
        assert insert_ref_other >= 1507

        # baseline comparison: remove the UPI row -> bank row alone counts 1507
        purge()
        insert_txns([{"description": f"UPI/TEST15_Gym Pro/{ref}", "amount": 1507,
                      "date": "21 Aug 2026", "source": "bank"}])
        base = client.get(f"{API}/financial/overview", timeout=60).json()
        assert _spend(base, "Other") == insert_ref_other, (
            f"donut differs: with pair={insert_ref_other}, bank-only={_spend(base,'Other')}")

    def test_exact_date_match_still_dedupes(self, client):
        ref = "111222333444"
        insert_txns([
            {"description": f"UPI/TEST15_Cafe/{ref}", "amount": 260, "date": "09 Aug 2026", "source": "bank"},
            {"description": "TEST15_Cafe", "amount": 260, "date": "09 Aug 2026", "source": "upi",
             "merchant": "TEST15_Cafe", "upi_ref": ref},
        ])
        od = client.get(f"{API}/financial/overview", timeout=60).json()
        rows = [t for t in od["transactions"] if t["amount"] == 260.0 and "cafe" in t["description"].lower()]
        assert len(rows) == 1, rows
        assert rows[0]["source"] == "upi"

    def test_bank_only_is_noop(self, client):
        ids = insert_txns([
            {"description": "TEST15_Bank A", "amount": 111, "date": "05 Aug 2026", "source": "bank"},
            {"description": "TEST15_Bank B", "amount": 222, "date": "06 Aug 2026", "source": "bank"},
            {"description": "TEST15_Bank C", "amount": 111, "date": "05 Aug 2026", "source": "bank"},
        ])
        od = client.get(f"{API}/financial/overview", timeout=60).json()
        got = {t["id"] for t in od["transactions"]}
        missing = [i for i in ids if i not in got]
        assert not missing, f"bank rows dropped with no UPI present: {missing}"

    @pytest.mark.parametrize("upi_over,label", [
        ({"amount": 1600, "date": "12 Aug 2026", "type": "Expense"}, "amount mismatch"),
        ({"amount": 1500, "date": "12 Aug 2026", "type": "Income"}, "type mismatch"),
        ({"amount": 1500, "date": "20 Aug 2026", "type": "Expense"}, "date gap > 3d"),
    ])
    def test_non_matching_pairs_both_kept(self, client, upi_over, label):
        ids = insert_txns([
            {"description": "TEST15_NoMatch bank", "amount": 1500, "date": "12 Aug 2026",
             "type": "Expense", "source": "bank"},
            {"description": "TEST15_NoMatch upi", "source": "upi", "merchant": "TEST15_NoMatch",
             **upi_over},
        ])
        od = client.get(f"{API}/financial/overview", timeout=60).json()
        got = {t["id"] for t in od["transactions"]}
        assert all(i in got for i in ids), f"{label}: row wrongly deduped"


    def test_possible_match_bank_row_behaviour(self, client):
        """A 'possible' (0.6 <= score < 0.85) pair: amount+type equal, 2-day gap, no ref,
        no description overlap. Documents whether analytics silently drops the bank row."""
        ids = insert_txns([
            {"description": "TEST15_Zzz Alpha", "amount": 1234, "date": "10 Aug 2026", "source": "bank"},
            {"description": "TEST15_Qqq Beta", "amount": 1234, "date": "12 Aug 2026", "source": "upi",
             "merchant": "TEST15_Qqq Beta"},
        ])
        v = client.get(f"{API}/statements/verify", timeout=60).json()
        pm = next((m for m in v["possible_matches"] if m["upi_txn"]["amount"] == 1234), None)
        if pm is None:
            pytest.skip("pair not scored as a possible match; heuristic changed")
        assert 0.6 <= pm["score"] < 0.85, pm
        od = client.get(f"{API}/financial/overview", timeout=60).json()
        got = {t["id"] for t in od["transactions"]}
        assert all(i in got for i in ids), (
            "UNCONFIRMED 'possible' match: bank row silently dropped from analytics "
            f"(score {pm['score']}). Only 'verified' (>=0.85) pairs should be deduped.")


# ---------------- verify payload hygiene ----------------
class TestVerifyPayload:
    def test_no_internal_fields_leaked(self, client):
        ref = "555666777888"
        insert_txns([
            {"description": f"UPI/TEST15_Leak/{ref}", "amount": 340, "date": "07 Aug 2026", "source": "bank"},
            {"description": "TEST15_Leak", "amount": 340, "date": "08 Aug 2026", "source": "upi",
             "merchant": "TEST15_Leak", "upi_ref": ref},
        ])
        r = client.get(f"{API}/statements/verify", timeout=60)
        assert r.status_code == 200
        d = r.json()
        assert "matched_bank_ids" not in d, "internal matched_bank_ids leaked to client"
        for key in ("verified_matches", "possible_matches", "upi_only", "bank_only", "counts", "months"):
            assert key in d, f"missing {key}"
        banned = {"_id", "user_id", "created_at", "verified_at"}
        checked = 0
        for m in d["verified_matches"] + d["possible_matches"]:
            for side in ("bank_txn", "upi_txn"):
                leaked = banned & set(m[side].keys())
                assert not leaked, f"{side} leaked {leaked}"
                checked += 1
        for t in d["upi_only"] + d["bank_only"]:
            leaked = banned & set(t.keys())
            assert not leaked, f"txn leaked {leaked}"
            checked += 1
        assert checked > 0, "no transactions inspected"
        c = d["counts"]
        for k in ("bank_total", "upi_total", "verified", "possible", "upi_only", "bank_only"):
            assert isinstance(c[k], int)


# ---------------- resolve-duplicate error paths ----------------
class TestResolveDuplicate:
    def _post(self, client, body):
        return client.post(f"{API}/statements/resolve-duplicate", json=body, timeout=30)

    def test_missing_drop_id_422(self, client):
        assert self._post(client, {"keep_id": "x"}).status_code == 422

    def test_missing_keep_id_422(self, client):
        assert self._post(client, {"drop_id": "x"}).status_code == 422

    def test_empty_body_422(self, client):
        assert self._post(client, {}).status_code == 422

    def test_same_id_400(self, client):
        ids = insert_txns([{"description": "TEST15_Same", "amount": 55, "date": "05 Aug 2026"}])
        r = self._post(client, {"keep_id": ids[0], "drop_id": ids[0]})
        assert r.status_code == 400, r.text[:200]

    def test_bogus_keep_valid_drop_404_and_no_delete(self, client):
        ids = insert_txns([{"description": "TEST15_Survivor", "amount": 66, "date": "05 Aug 2026"}])
        r = self._post(client, {"keep_id": f"bogus-{uuid.uuid4()}", "drop_id": ids[0]})
        assert r.status_code == 404, r.text[:200]
        ov = client.get(f"{API}/financial/overview", timeout=60).json()
        assert any(t["id"] == ids[0] for t in ov["transactions"]), "drop row deleted despite 404"

    def test_valid_keep_bogus_drop_404(self, client):
        ids = insert_txns([{"description": "TEST15_Keeper", "amount": 77, "date": "05 Aug 2026"}])
        r = self._post(client, {"keep_id": ids[0], "drop_id": f"bogus-{uuid.uuid4()}"})
        assert r.status_code == 404, r.text[:200]

    def test_happy_path_200(self, client):
        ref = "121212121212"
        ids = insert_txns([
            {"description": f"UPI/TEST15_Happy/{ref}", "amount": 890, "date": "04 Aug 2026", "source": "bank"},
            {"description": "TEST15_Happy", "amount": 890, "date": "05 Aug 2026", "source": "upi",
             "merchant": "TEST15_Happy", "upi_ref": ref},
        ])
        bank_id, upi_id = ids
        r = self._post(client, {"keep_id": upi_id, "drop_id": bank_id})
        assert r.status_code == 200, r.text[:300]
        body = r.json()
        assert body["deleted"] == 1
        assert body["verified"] == upi_id
        ov = client.get(f"{API}/financial/overview", timeout=60).json()
        rows = [t for t in ov["transactions"] if t["amount"] == 890.0]
        assert len(rows) == 1 and rows[0]["id"] == upi_id
        assert rows[0].get("verified") is True

    def test_requires_auth(self):
        r = requests.post(f"{API}/statements/resolve-duplicate",
                          json={"keep_id": "a", "drop_id": "b"}, timeout=30)
        assert r.status_code in (401, 403)


# ---------------- misc regressions ----------------
class TestMiscRegressions:
    def test_auth_me_has_picture(self, client):
        r = client.get(f"{API}/auth/me", timeout=30)
        assert r.status_code == 200, r.text[:200]
        assert "picture" in r.json(), r.json().keys()

    def test_google_start_302(self):
        r = requests.get(f"{API}/auth/google/start", allow_redirects=False, timeout=30)
        assert r.status_code == 302, r.status_code
        assert "accounts.google.com" in r.headers.get("location", "")

    def test_overview_shape(self, client):
        r = client.get(f"{API}/financial/overview", timeout=60)
        assert r.status_code == 200
        d = r.json()
        for k in ("summary", "history", "transactions", "goals", "spending"):
            assert k in d, f"missing {k}"
        for t in d["transactions"]:
            assert "_id" not in t
