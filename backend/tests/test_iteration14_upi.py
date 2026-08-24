"""Iteration 14 — UPI Statement Reader + cross-verification tests.

Modules covered:
  - /api/statements/preview (source=upi)
  - /api/statements/parse   (source=upi, Google Pay style CSV)
  - /api/statements/confirm-import (source=upi metadata persistence)
  - /api/statements/verify (cross verification categories)
  - /api/statements/resolve-duplicate
  - /api/financial/overview cross-source dedupe
  - regressions: bank flow, goals, whatif, settings, google oauth start
"""
import io
import os
import re
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

# Dates deliberately aligned with the seeded demo bank rows
# (SWIGGY 450 12 Aug, RENT PAYMENT 32000 03 Aug, AMAZON INDIA 3890 06 Aug)
UPI_CSV = """Transaction Time,To / From,Amount,Type,Transaction ID,UPI Ref No,UPI ID
12 Aug 2026,Swiggy,450,Sent,GP2026081201,412345678901,swiggy@ybl
03 Aug 2026,Landlord Rent,32000,Sent,GP2026081202,412345678902,landlord@okhdfcbank
06 Aug 2026,Amazon,3890,Sent,GP2026081203,412345678903,amazon@apl
"""

# CSV where positive amount + merchant column + no type hint -> should default Expense
UPI_CSV_NO_TYPE = """Date,Merchant,Amount,Transaction ID
12 Aug 2026,Swiggy,450,NT1
"""

BANK_CSV = """Date,Narration,Debit,Credit
05 Aug 2026,STARBUCKS COFFEE MG ROAD,320,
06 Aug 2026,SALARY AUG,,185000
"""


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


def _files(csv_text, name="upi.csv"):
    return {"file": (name, io.BytesIO(csv_text.encode()), "text/csv")}


def db_cleanup(patterns=("TEST_",), drop_upi=True):
    """Direct-DB cleanup: there is no DELETE /api/transactions/{id} endpoint."""
    import asyncio
    from motor.motor_asyncio import AsyncIOMotorClient
    env = dotenv_values("/app/backend/.env")

    async def _run():
        db = AsyncIOMotorClient(env["MONGO_URL"])[env["DB_NAME"]]
        u = await db.users.find_one({"email": "testuser@finaura.dev"})
        uid = str(u["_id"])
        ors = [{"description": {"$regex": p, "$options": "i"}} for p in patterns]
        if drop_upi:
            ors.append({"source": "upi"})
        r = await db.finaura_transactions.delete_many({"user_id": uid, "$or": ors})
        return r.deleted_count

    return asyncio.new_event_loop().run_until_complete(_run())


@pytest.fixture(scope="session")
def imported_upi_ids(client):
    """Import the 3-row google-pay CSV once; cleanup at session end."""
    import json
    db_cleanup()
    prev = client.post(f"{API}/statements/preview", files=_files(UPI_CSV), data={"source": "upi"}, timeout=60)
    assert prev.status_code == 200, prev.text[:300]
    mapping = prev.json()["guess"]
    parsed = client.post(f"{API}/statements/parse", files=_files(UPI_CSV),
                         data={"mapping": json.dumps(mapping), "source": "upi"}, timeout=60)
    assert parsed.status_code == 200, parsed.text[:300]
    txns = parsed.json()["transactions"]
    imp = client.post(f"{API}/statements/confirm-import", json={"transactions": txns, "source": "upi"}, timeout=60)
    assert imp.status_code == 200, imp.text[:300]
    yield txns
    db_cleanup()


# ---------------- preview ----------------
class TestUpiPreview:
    def test_preview_upi_guess_has_upi_fields(self, client):
        r = client.post(f"{API}/statements/preview", files=_files(UPI_CSV), data={"source": "upi"}, timeout=60)
        assert r.status_code == 200, r.text[:300]
        d = r.json()
        assert d["source"] == "upi"
        assert d["kind"] == "csv"
        g = d["guess"]
        for k in ("merchant", "upi_id", "upi_ref", "txn_id"):
            assert k in g, f"{k} missing from guess"
            assert g[k], f"{k} not auto-mapped: {g}"
        assert g["txn_id"] == "Transaction ID"
        assert g["upi_id"] == "UPI ID"
        assert g["upi_ref"] == "UPI Ref No"
        assert d["total_rows"] == 3

    def test_preview_bank_has_no_upi_fields(self, client):
        r = client.post(f"{API}/statements/preview", files=_files(BANK_CSV, "bank.csv"), data={"source": "bank"}, timeout=60)
        assert r.status_code == 200
        d = r.json()
        assert d["source"] == "bank"
        assert "merchant" not in d["guess"]

    def test_preview_requires_auth(self):
        r = requests.post(f"{API}/statements/preview", files=_files(UPI_CSV), data={"source": "upi"}, timeout=30)
        assert r.status_code in (401, 403)


# ---------------- parse ----------------
class TestUpiParse:
    def test_parse_upi_fields_and_categories(self, client):
        import json
        prev = client.post(f"{API}/statements/preview", files=_files(UPI_CSV), data={"source": "upi"}, timeout=60)
        mapping = prev.json()["guess"]
        r = client.post(f"{API}/statements/parse", files=_files(UPI_CSV),
                        data={"mapping": json.dumps(mapping), "source": "upi"}, timeout=60)
        assert r.status_code == 200, r.text[:300]
        d = r.json()
        assert d["source"] == "upi"
        txns = d["transactions"]
        assert len(txns) == 3, txns
        by_desc = {t["description"]: t for t in txns}
        assert set(by_desc) == {"Swiggy", "Landlord Rent", "Amazon"}
        assert by_desc["Swiggy"]["category"] == "Food"
        assert by_desc["Amazon"]["category"] == "Shopping"
        assert by_desc["Landlord Rent"]["category"] == "Rent"
        for t in txns:
            assert t["source"] == "upi"
            assert t["type"] == "Expense", t
            assert t["merchant"]
            assert t["txn_id"]
            assert t["upi_ref"]
            assert t["upi_id"]
        assert by_desc["Swiggy"]["amount"] == 450
        assert by_desc["Swiggy"]["date"] == "12 Aug 2026"
        assert by_desc["Swiggy"]["upi_id"] == "swiggy@ybl"
        assert by_desc["Swiggy"]["txn_id"] == "GP2026081201"
        assert by_desc["Swiggy"]["upi_ref"] == "412345678901"

    def test_positive_amount_no_type_hint_defaults_expense(self, client):
        import json
        prev = client.post(f"{API}/statements/preview", files=_files(UPI_CSV_NO_TYPE), data={"source": "upi"}, timeout=60)
        mapping = prev.json()["guess"]
        assert mapping.get("merchant"), mapping
        r = client.post(f"{API}/statements/parse", files=_files(UPI_CSV_NO_TYPE),
                        data={"mapping": json.dumps(mapping), "source": "upi"}, timeout=60)
        assert r.status_code == 200
        txns = r.json()["transactions"]
        assert len(txns) == 1
        assert txns[0]["type"] == "Expense", txns[0]
        assert txns[0]["category"] == "Food"

    def test_parse_invalid_mapping_json_400(self, client):
        r = client.post(f"{API}/statements/parse", files=_files(UPI_CSV),
                        data={"mapping": "{oops", "source": "upi"}, timeout=60)
        assert r.status_code == 400


# ---------------- confirm-import + verify ----------------
class TestImportAndVerify:
    def test_import_persists_upi_metadata(self, client, imported_upi_ids):
        ov = client.get(f"{API}/financial/overview", timeout=60)
        assert ov.status_code == 200
        upi = [t for t in ov.json()["transactions"] if t.get("source") == "upi"]
        assert len(upi) >= 3
        swig = [t for t in upi if t["description"] == "Swiggy"]
        assert swig, upi
        t = swig[0]
        assert t["upi_id"] == "swiggy@ybl"
        assert t["txn_id"] == "GP2026081201"
        assert t["upi_ref"] == "412345678901"
        assert t["merchant"] == "Swiggy"
        assert "_id" not in t

    def test_verify_shape_and_verified_matches(self, client, imported_upi_ids):
        r = client.get(f"{API}/statements/verify", timeout=60)
        assert r.status_code == 200, r.text[:300]
        d = r.json()
        for k in ("counts", "verified_matches", "possible_matches", "upi_only", "bank_only", "months"):
            assert k in d, k
        c = d["counts"]
        for k in ("bank_total", "upi_total", "verified", "possible", "upi_only", "bank_only"):
            assert k in c
        assert c["upi_total"] >= 3
        assert c["verified"] >= 3, d["counts"]
        pairs = {(m["bank_txn"]["description"], m["upi_txn"]["description"], m["score"])
                 for m in d["verified_matches"]}
        matched_amounts = {m["upi_txn"]["amount"] for m in d["verified_matches"]}
        assert 450 in matched_amounts and 32000 in matched_amounts and 3890 in matched_amounts, pairs
        for m in d["verified_matches"]:
            assert m["score"] >= 0.85, m
            assert m["status"] == "verified"
            assert m["bank_txn"].get("source", "bank") == "bank"
            assert m["upi_txn"]["source"] == "upi"
        # no mongo internals should leak
        for m in d["verified_matches"]:
            for side in ("bank_txn", "upi_txn"):
                assert "_id" not in m[side], f"_id leaked in {side}: {list(m[side].keys())}"
                assert "user_id" not in m[side], f"user_id leaked in {side}"
        assert isinstance(d["months"], list) and d["months"]
        assert any(mo["upi_count"] >= 3 for mo in d["months"])

    def test_verify_month_filter(self, client, imported_upi_ids):
        r = client.get(f"{API}/statements/verify", params={"month": "Aug 2026"}, timeout=60)
        assert r.status_code == 200
        d = r.json()
        assert d["counts"]["upi_total"] >= 3
        r2 = client.get(f"{API}/statements/verify", params={"month": "Jan 1990"}, timeout=60)
        assert r2.status_code == 200
        assert r2.json()["counts"]["bank_total"] == 0
        assert r2.json()["counts"]["upi_total"] == 0

    def test_verify_requires_auth(self):
        r = requests.get(f"{API}/statements/verify", timeout=30)
        assert r.status_code in (401, 403)

    def test_overview_dedupes_across_sources(self, client, imported_upi_ids):
        ov = client.get(f"{API}/financial/overview", timeout=60)
        assert ov.status_code == 200
        txns = ov.json()["transactions"]
        sigs = [(t["amount"], t["date"], t["type"]) for t in txns]
        dupes = {s for s in sigs if sigs.count(s) > 1}
        assert not dupes, f"double counted signatures present: {dupes}"
        # each of the 3 UPI amounts appears exactly once
        for amt in (450.0, 32000.0, 3890.0):
            occ = [t for t in txns if t["amount"] == amt and t["date"].endswith("Aug 2026")]
            assert len(occ) == 1, f"{amt} counted {len(occ)} times: {occ}"
            assert occ[0]["source"] == "upi"
        spend = {s["name"]: s["value"] for s in ov.json().get("spending", [])}
        assert spend, "spending empty"


class TestResolveDuplicate:
    def test_resolve_duplicate_deletes_and_tags(self, client):
        import json
        csv = "Transaction Time,To / From,Amount,Type,Transaction ID,UPI Ref No,UPI ID\n20 Aug 2026,TEST_Dedupe Shop,777,Sent,TESTDD1,999888777666,testdd@ybl\n"
        mapping = client.post(f"{API}/statements/preview", files=_files(csv), data={"source": "upi"}, timeout=60).json()["guess"]
        txns = client.post(f"{API}/statements/parse", files=_files(csv),
                           data={"mapping": json.dumps(mapping), "source": "upi"}, timeout=60).json()["transactions"]
        assert client.post(f"{API}/statements/confirm-import", json={"transactions": txns, "source": "upi"}, timeout=60).status_code == 200
        # also import a bank twin
        bank_csv = "Date,Narration,Debit,Credit\n20 Aug 2026,UPI/TEST_Dedupe Shop/999888777666,777,\n"
        bmap = client.post(f"{API}/statements/preview", files=_files(bank_csv, "b.csv"), data={"source": "bank"}, timeout=60).json()["guess"]
        btxns = client.post(f"{API}/statements/parse", files=_files(bank_csv, "b.csv"),
                            data={"mapping": json.dumps(bmap), "source": "bank"}, timeout=60).json()["transactions"]
        assert client.post(f"{API}/statements/confirm-import", json={"transactions": btxns, "source": "bank"}, timeout=60).status_code == 200

        v = client.get(f"{API}/statements/verify", timeout=60).json()
        match = next((m for m in v["verified_matches"] + v["possible_matches"]
                      if m["upi_txn"]["amount"] == 777), None)
        assert match, "777 pair not matched by cross_verify"
        keep, drop = match["upi_txn"]["id"], match["bank_txn"]["id"]
        r = client.post(f"{API}/statements/resolve-duplicate", json={"keep_id": keep, "drop_id": drop}, timeout=60)
        assert r.status_code == 200, r.text[:300]
        assert r.json()["deleted"] == 1
        assert r.json()["verified"] == keep

        v2 = client.get(f"{API}/statements/verify", timeout=60).json()
        ids = {t["id"] for t in v2["bank_only"]} | {m["bank_txn"]["id"] for m in v2["verified_matches"] + v2["possible_matches"]}
        assert drop not in ids, "dropped bank txn still present"

        ov = client.get(f"{API}/financial/overview", timeout=60).json()
        occ = [t for t in ov["transactions"] if t["amount"] == 777.0]
        assert len(occ) == 1, occ
        assert occ[0].get("verified") is True, occ[0]
        # cleanup
        db_cleanup(patterns=("TEST_Dedupe",))

    def test_resolve_duplicate_foreign_id_404(self, client):
        r = client.post(f"{API}/statements/resolve-duplicate",
                        json={"keep_id": "nope-1", "drop_id": "nope-2"}, timeout=30)
        assert r.status_code == 404, r.text[:200]

    def test_resolve_duplicate_missing_fields_400(self, client):
        r = client.post(f"{API}/statements/resolve-duplicate", json={"keep_id": "x"}, timeout=30)
        assert r.status_code == 400

    def test_resolve_duplicate_requires_auth(self):
        r = requests.post(f"{API}/statements/resolve-duplicate", json={"keep_id": "a", "drop_id": "b"}, timeout=30)
        assert r.status_code in (401, 403)


# ---------------- regressions ----------------
class TestRegressions:
    def test_bank_flow_still_works(self, client):
        import json
        prev = client.post(f"{API}/statements/preview", files=_files(BANK_CSV, "bank.csv"), data={"source": "bank"}, timeout=60)
        assert prev.status_code == 200
        g = prev.json()["guess"]
        assert g["date"] and g["description"] and g["debit"] and g["credit"]
        parsed = client.post(f"{API}/statements/parse", files=_files(BANK_CSV, "bank.csv"),
                             data={"mapping": json.dumps(g), "source": "bank"}, timeout=60)
        assert parsed.status_code == 200
        txns = parsed.json()["transactions"]
        assert len(txns) == 2
        for t in txns:
            assert t["source"] == "bank"
        sal = [t for t in txns if "SALARY" in t["description"]][0]
        assert sal["type"] == "Income" and sal["amount"] == 185000
        cof = [t for t in txns if "STARBUCKS" in t["description"]][0]
        assert cof["type"] == "Expense" and cof["category"] == "Food"
        # import without source field (legacy body)
        imp = client.post(f"{API}/statements/confirm-import", json={"transactions": txns}, timeout=60)
        assert imp.status_code == 200
        assert imp.json()["source"] == "bank"
        assert imp.json()["imported"] == 2
        ov = client.get(f"{API}/financial/overview", timeout=60).json()
        newly = [t for t in ov["transactions"] if "STARBUCKS" in t["description"] or "SALARY AUG" == t["description"]]
        assert len(newly) == 2
        for t in newly:
            assert t.get("source", "bank") == "bank"
        db_cleanup(patterns=("STARBUCKS COFFEE MG ROAD", "SALARY AUG"), drop_upi=False)

    def test_no_double_count_when_dates_differ_by_days(self, client):
        """Cross-source dedupe must cover the same window as /verify (±3 days)."""
        import json
        upi_csv = ("Transaction Time,To / From,Amount,Type,Transaction ID,UPI Ref No,UPI ID\n"
                   "12 Aug 2026,TEST_Gym Pro,1500,Sent,TESTG1,555444333222,gympro@ybl\n")
        bank_csv = ("Date,Narration,Debit,Credit\n"
                    "11 Aug 2026,UPI/TEST_Gym Pro/555444333222,1500,\n")
        for csv_text, src, fname in ((upi_csv, "upi", "u.csv"), (bank_csv, "bank", "b.csv")):
            g = client.post(f"{API}/statements/preview", files=_files(csv_text, fname), data={"source": src}, timeout=60).json()["guess"]
            t = client.post(f"{API}/statements/parse", files=_files(csv_text, fname),
                            data={"mapping": json.dumps(g), "source": src}, timeout=60).json()["transactions"]
            assert client.post(f"{API}/statements/confirm-import", json={"transactions": t, "source": src}, timeout=60).status_code == 200
        v = client.get(f"{API}/statements/verify", timeout=60).json()
        pair = next((m for m in v["verified_matches"] if m["upi_txn"]["amount"] == 1500), None)
        assert pair, "1-day-apart pair not flagged verified by /verify"
        try:
            ov = client.get(f"{API}/financial/overview", timeout=60).json()
            occ = [t for t in ov["transactions"] if t["amount"] == 1500.0]
            assert len(occ) == 1, f"verified duplicate double-counted in analytics: {occ}"
        finally:
            db_cleanup(patterns=("TEST_Gym",), drop_upi=False)

    def test_goals_list(self, client):
        r = client.get(f"{API}/financial/overview", timeout=60)
        assert r.status_code == 200
        assert isinstance(r.json()["goals"], list)

    def test_whatif_subscription(self, client):
        r = client.post(f"{API}/whatif/subscription", json={"item_name": "Test SaaS", "monthly_cost": 999, "onetime_cost": 15000, "years": 5}, timeout=60)
        assert r.status_code == 200, r.text[:300]
        assert "subscription" in r.json() and "onetime" in r.json()

    def test_whatif_twin(self, client):
        r = client.post(f"{API}/whatif/twin", json={"monthly_savings": 25000, "annual_return": 12}, timeout=60)
        assert r.status_code == 200, r.text[:300]
        assert "baseline" in r.json()

    def test_settings_preferences(self, client):
        r = client.get(f"{API}/settings/preferences", timeout=30)
        assert r.status_code == 200

    def test_google_start_redirects(self):
        r = requests.get(f"{API}/auth/google/start", allow_redirects=False, timeout=30)
        assert r.status_code in (302, 307)

    def test_demo_overview_public(self):
        r = requests.get(f"{API}/demo/overview", timeout=30)
        assert r.status_code == 200
        assert "transactions" in r.json()
