"""Iteration 16 — verified-vs-possible dedupe boundary + legacy docs without `id`.

Modules covered:
  - statements.cross_verify / statements.dedupe_across_sources (pure unit tests, score boundary)
  - GET /api/statements/verify        (possible vs verified classification, no internal leaks)
  - GET /api/financial/overview       (only score>=0.85 pairs deduped)
  - legacy transaction doc with no `id` field must not 500 either endpoint
"""
import asyncio
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
import requests
from dotenv import dotenv_values
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, "/app/backend")
from statements import cross_verify, dedupe_across_sources, _match_score  # noqa: E402

frontend_env = dotenv_values("/app/frontend/.env")
base_url = os.environ.get("REACT_APP_BACKEND_URL") or frontend_env.get("REACT_APP_BACKEND_URL")
if not base_url:
    raise RuntimeError("REACT_APP_BACKEND_URL missing")
BASE_URL = base_url.rstrip("/")
API = f"{BASE_URL}/api"
BACKEND_ENV = dotenv_values("/app/backend/.env")
TEST_EMAIL = "testuser@finaura.dev"
PREFIX = "TEST16_"


# ---------------- fixtures / helpers ----------------
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
        pytest.fail(f"no token: {r.text[:300]}")
    s.headers.update({"Authorization": f"Bearer {token}"})
    return s


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


async def _db():
    return AsyncIOMotorClient(BACKEND_ENV["MONGO_URL"])[BACKEND_ENV["DB_NAME"]]


def insert_txns(docs, with_id=True):
    async def _go():
        db = await _db()
        u = await db.users.find_one({"email": TEST_EMAIL})
        uid = str(u["_id"])
        rows = []
        for d in docs:
            row = {
                "user_id": uid,
                "created_at": datetime.now(timezone.utc),
                "type": "Expense",
                "category": "Other",
                "source": "bank",
                **d,
            }
            if with_id and "id" not in row:
                row["id"] = str(uuid.uuid4())
            rows.append(row)
        await db.finaura_transactions.insert_many(rows)
        return [r.get("id") for r in rows]
    return _run(_go())


def purge():
    async def _go():
        db = await _db()
        u = await db.users.find_one({"email": TEST_EMAIL})
        r = await db.finaura_transactions.delete_many(
            {"user_id": str(u["_id"]), "description": {"$regex": PREFIX, "$options": "i"}})
        return r.deleted_count
    return _run(_go())


@pytest.fixture(autouse=True)
def clean_around():
    """Cross-worker mutex (see test_iteration15_dedupe.py): shared backend + shared test
    user + hardcoded amounts means these tests must not run concurrently."""
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


# ---------------- unit: score boundary ----------------
class TestScoreBoundaryUnit:
    def _pair(self, bank_desc, upi_desc, bank_date, upi_date, upi_extra=None):
        bank = {"id": "b1", "source": "bank", "type": "Expense", "amount": 500,
                "date": bank_date, "description": bank_desc}
        upi = {"id": "u1", "source": "upi", "type": "Expense", "amount": 500,
               "date": upi_date, "description": upi_desc}
        if upi_extra:
            upi.update(upi_extra)
        return bank, upi

    def test_ref_match_1day_scores_exactly_085_and_dedupes(self):
        """ref present + 1 day gap == 0.6 + 0.25 = 0.85 -> verified (boundary, inclusive)."""
        ref = "900011112222"
        bank, upi = self._pair(f"UPI/Zzzz/{ref}", "Qqqq shop", "01 Sep 2026", "02 Sep 2026",
                               {"upi_ref": ref})
        score, _ = _match_score(bank, upi)
        assert round(score, 2) == 0.85, score
        cv = cross_verify([bank], [upi])
        assert cv["counts"]["verified"] == 1 and cv["counts"]["possible"] == 0, cv["counts"]
        assert cv["verified_bank_ids"] == ["b1"]
        kept = dedupe_across_sources([bank, upi])
        assert [t["id"] for t in kept] == ["u1"], kept

    def test_score_075_is_possible_and_not_deduped(self):
        """token overlap only + 3 day gap == 0.6 + 0.15 = 0.75 -> possible, keep both."""
        bank, upi = self._pair("Zzzz Coffee House", "Coffee House", "01 Sep 2026", "04 Sep 2026")
        score, _ = _match_score(bank, upi)
        assert 0.6 <= round(score, 2) < 0.85, score
        cv = cross_verify([bank], [upi])
        assert cv["counts"]["possible"] == 1 and cv["counts"]["verified"] == 0, cv["counts"]
        assert cv["verified_bank_ids"] == []
        kept = {t["id"] for t in dedupe_across_sources([bank, upi])}
        assert kept == {"b1", "u1"}, kept

    def test_bare_amount_date_match_scores_06_possible(self):
        bank, upi = self._pair("Zzzz Alpha", "Qqqq Beta", "01 Sep 2026", "03 Sep 2026")
        score, _ = _match_score(bank, upi)
        assert round(score, 2) == 0.6, score
        kept = {t["id"] for t in dedupe_across_sources([bank, upi])}
        assert kept == {"b1", "u1"}

    def test_synthetic_084_not_deduped(self, monkeypatch):
        """Guard the boundary with a synthetic 0.84 score (unreachable via heuristics)."""
        import statements as st
        bank, upi = self._pair("Zzzz Alpha", "Qqqq Beta", "01 Sep 2026", "01 Sep 2026")
        monkeypatch.setattr(st, "_match_score", lambda b, u: (0.84, "synthetic"))
        cv = st.cross_verify([bank], [upi])
        assert cv["counts"]["possible"] == 1 and cv["counts"]["verified"] == 0
        kept = {t["id"] for t in st.dedupe_across_sources([bank, upi])}
        assert kept == {"b1", "u1"}, "0.84 must NOT dedupe"

    def test_synthetic_085_deduped(self, monkeypatch):
        import statements as st
        bank, upi = self._pair("Zzzz Alpha", "Qqqq Beta", "01 Sep 2026", "01 Sep 2026")
        monkeypatch.setattr(st, "_match_score", lambda b, u: (0.85, "synthetic"))
        cv = st.cross_verify([bank], [upi])
        assert cv["counts"]["verified"] == 1
        assert [t["id"] for t in st.dedupe_across_sources([bank, upi])] == ["u1"]

    def test_one_upi_cannot_consume_two_bank_rows(self):
        b1 = {"id": "b1", "source": "bank", "type": "Expense", "amount": 500,
              "date": "01 Sep 2026", "description": "Zzzz Alpha"}
        b2 = {"id": "b2", "source": "bank", "type": "Expense", "amount": 500,
              "date": "01 Sep 2026", "description": "Zzzz Alpha two"}
        upi = {"id": "u1", "source": "upi", "type": "Expense", "amount": 500,
               "date": "01 Sep 2026", "description": "Zzzz Alpha"}
        cv = cross_verify([b1, b2], [upi])
        assert cv["counts"]["verified"] + cv["counts"]["possible"] == 1
        assert cv["counts"]["bank_only"] == 1
        # only the verified bank row (if any) is deduped
        kept = {t["id"] for t in dedupe_across_sources([b1, b2, upi])}
        assert "u1" in kept and len(kept) >= 2, kept

    def test_missing_id_docs_are_skipped_not_crashing(self):
        legacy_bank = {"source": "bank", "type": "Expense", "amount": 500,
                       "date": "01 Sep 2026", "description": "Legacy bank"}
        legacy_upi = {"source": "upi", "type": "Expense", "amount": 500,
                      "date": "01 Sep 2026", "description": "Legacy bank"}
        cv = cross_verify([legacy_bank], [legacy_upi])
        assert cv["counts"]["verified"] == 0 and cv["counts"]["possible"] == 0
        kept = dedupe_across_sources([legacy_bank, legacy_upi])
        assert len(kept) == 2, "legacy rows must be preserved"


# ---------------- API: boundary behaviour end-to-end ----------------
class TestBoundaryApi:
    def test_verified_pair_deduped_possible_pair_kept(self, client):
        ref = "778899001122"
        ids = insert_txns([
            # verified: ref in bank narration, 1 day gap -> 0.85
            {"description": f"UPI/{PREFIX}Verified/{ref}", "amount": 4321,
             "date": "02 Sep 2026", "source": "bank"},
            {"description": f"{PREFIX}Verified", "amount": 4321, "date": "03 Sep 2026",
             "source": "upi", "merchant": f"{PREFIX}Verified", "upi_ref": ref},
            # possible: same amount, 2 day gap, unrelated names -> 0.6
            {"description": f"{PREFIX}Wwww Alpha", "amount": 8765, "date": "10 Sep 2026",
             "source": "bank"},
            {"description": f"{PREFIX}Yyyy Beta", "amount": 8765, "date": "12 Sep 2026",
             "source": "upi", "merchant": f"{PREFIX}Yyyy Beta"},
        ])
        v_bank, v_upi, p_bank, p_upi = ids

        v = client.get(f"{API}/statements/verify", timeout=60)
        assert v.status_code == 200, v.text[:300]
        vd = v.json()
        vm = next((m for m in vd["verified_matches"] if m["upi_txn"]["amount"] == 4321), None)
        pm = next((m for m in vd["possible_matches"] if m["upi_txn"]["amount"] == 8765), None)
        assert vm and vm["score"] >= 0.85, vd["counts"]
        assert pm and 0.6 <= pm["score"] < 0.85, vd["counts"]

        ov = client.get(f"{API}/financial/overview", timeout=60)
        assert ov.status_code == 200, ov.text[:300]
        got = {t["id"] for t in ov["transactions"]} if False else {t["id"] for t in ov.json()["transactions"]}
        assert v_bank not in got, "verified bank row should be deduped"
        assert v_upi in got
        assert p_bank in got and p_upi in got, "possible-match rows must both survive"

    def test_verify_does_not_leak_internal_id_sets(self, client):
        insert_txns([
            {"description": f"{PREFIX}Leak bank", "amount": 999, "date": "05 Sep 2026", "source": "bank"},
            {"description": f"{PREFIX}Leak bank", "amount": 999, "date": "05 Sep 2026", "source": "upi"},
        ])
        d = client.get(f"{API}/statements/verify", timeout=60).json()
        assert "matched_bank_ids" not in d
        assert "verified_bank_ids" not in d
        for key in ("verified_matches", "possible_matches", "upi_only", "bank_only", "counts", "months"):
            assert key in d, f"missing {key}"
        banned = {"_id", "user_id", "created_at", "verified_at"}
        for m in d["verified_matches"] + d["possible_matches"]:
            for side in ("bank_txn", "upi_txn"):
                assert not banned & set(m[side]), m[side].keys()
        for t in d["upi_only"] + d["bank_only"]:
            assert not banned & set(t), t.keys()

    def test_legacy_doc_without_id_does_not_break_endpoints(self, client):
        insert_txns([
            {"description": f"{PREFIX}Legacy no id", "amount": 6543, "date": "15 Sep 2026",
             "source": "bank"},
            {"description": f"{PREFIX}Legacy no id", "amount": 6543, "date": "15 Sep 2026",
             "source": "upi"},
        ], with_id=False)
        v = client.get(f"{API}/statements/verify", timeout=60)
        assert v.status_code == 200, f"/verify 500s on legacy docs: {v.text[:300]}"
        ov = client.get(f"{API}/financial/overview", timeout=60)
        assert ov.status_code == 200, f"/overview 500s on legacy docs: {ov.text[:300]}"
        # legacy rows without id must be skipped by the matcher (never deduped)
        legacy_rows = [t for t in ov.json()["transactions"]
                       if t.get("amount") == 6543.0 and "legacy no id" in t.get("description", "").lower()]
        assert len(legacy_rows) == 2, f"legacy rows lost: {legacy_rows}"
        for t in legacy_rows:
            assert "_id" not in t

    def test_verify_month_scoping_unaffected(self, client):
        insert_txns([
            {"description": f"{PREFIX}Month bank", "amount": 1212, "date": "18 Sep 2026", "source": "bank"},
        ])
        d = client.get(f"{API}/statements/verify", params={"month": "Sep 2026"}, timeout=60).json()
        assert any(t.get("description", "").startswith(f"{PREFIX}Month") for t in d["bank_only"])
        other = client.get(f"{API}/statements/verify", params={"month": "Jan 1999"}, timeout=60).json()
        assert other["counts"]["bank_total"] == 0 and other["counts"]["upi_total"] == 0


# ---------------- resolve-duplicate regression ----------------
class TestResolveDuplicateRegression:
    def _post(self, client, body):
        return client.post(f"{API}/statements/resolve-duplicate", json=body, timeout=30)

    def test_422_400_404_and_happy_path(self, client):
        assert self._post(client, {}).status_code == 422
        assert self._post(client, {"keep_id": "a"}).status_code == 422
        ids = insert_txns([{"description": f"{PREFIX}Res same", "amount": 121, "date": "06 Sep 2026"}])
        assert self._post(client, {"keep_id": ids[0], "drop_id": ids[0]}).status_code == 400
        assert self._post(client, {"keep_id": ids[0], "drop_id": f"bogus-{uuid.uuid4()}"}).status_code == 404
        assert self._post(client, {"keep_id": f"bogus-{uuid.uuid4()}", "drop_id": ids[0]}).status_code == 404
        ov = client.get(f"{API}/financial/overview", timeout=60).json()
        assert any(t["id"] == ids[0] for t in ov["transactions"]), "row deleted despite 404"

        ref = "313131313131"
        pair = insert_txns([
            {"description": f"UPI/{PREFIX}Res/{ref}", "amount": 1919, "date": "07 Sep 2026", "source": "bank"},
            {"description": f"{PREFIX}Res", "amount": 1919, "date": "08 Sep 2026", "source": "upi",
             "merchant": f"{PREFIX}Res", "upi_ref": ref},
        ])
        r = self._post(client, {"keep_id": pair[1], "drop_id": pair[0]})
        assert r.status_code == 200, r.text[:300]
        assert r.json()["deleted"] == 1 and r.json()["verified"] == pair[1]
        ov = client.get(f"{API}/financial/overview", timeout=60).json()
        rows = [t for t in ov["transactions"] if t["amount"] == 1919.0]
        assert len(rows) == 1 and rows[0]["id"] == pair[1] and rows[0].get("verified") is True
