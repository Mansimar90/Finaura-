"""Iteration 19 — independent verification of the 4 fixes flagged in iteration_18.json.

Covers (via the LIVE public API only, fresh isolated users):
  FIX 1  parse_csv: bank CSV with a single unsigned Amount column -> debits are Expense
  FIX 2a _match_score veto on conflicting upi_ref/txn_id (no silent expense deletion)
  FIX 2b generic single shared token no longer auto-merges; real merchant match still does
  FIX 3  DELETE /api/financial/data purges finaura_memories (also gone from settings/export)
  FIX 4  _auto_map_columns short aliases cr/dr match on word boundaries only
"""
import io
import json
import os
import uuid

import pytest
import requests
from dotenv import dotenv_values

frontend_env = dotenv_values("/app/frontend/.env")
base_url = os.environ.get("REACT_APP_BACKEND_URL") or frontend_env.get("REACT_APP_BACKEND_URL")
if not base_url:
    raise RuntimeError("REACT_APP_BACKEND_URL missing from env and /app/frontend/.env")
API = f"{base_url.rstrip('/')}/api"


def _files(csv_text, name="stmt.csv"):
    return {"file": (name, io.BytesIO(csv_text.encode()), "text/csv")}


def _new_client():
    s = requests.Session()
    email = f"it19_{uuid.uuid4().hex[:10]}@qa.finaura.dev"
    r = s.post(f"{API}/auth/register",
               json={"email": email, "password": "TestPass123!", "name": "TEST_it19"}, timeout=60)
    if r.status_code not in (200, 201):
        pytest.fail(f"register failed {r.status_code}: {r.text[:300]}")
    token = r.json().get("token") or r.json().get("access_token")
    assert token, f"no token in register response: {r.text[:300]}"
    s.headers.update({"Authorization": f"Bearer {token}"})
    return s, email


def _teardown(s):
    try:
        s.delete(f"{API}/financial/data", timeout=60)
        s.delete(f"{API}/auth/account", timeout=60)
    except Exception:
        pass


def _import(s, csv, source):
    guess = s.post(f"{API}/statements/preview", files=_files(csv),
                   data={"source": source}, timeout=60)
    assert guess.status_code == 200, guess.text[:300]
    mapping = guess.json()["guess"]
    parsed = s.post(f"{API}/statements/parse", files=_files(csv),
                    data={"mapping": json.dumps(mapping), "source": source}, timeout=60)
    assert parsed.status_code == 200, parsed.text[:300]
    txns = parsed.json()["transactions"]
    conf = s.post(f"{API}/statements/confirm-import",
                  json={"transactions": txns, "source": source}, timeout=60)
    assert conf.status_code == 200, conf.text[:300]
    return txns, conf.json()


# ---------------- FIX 1: bank single Amount column direction ----------------
class TestFix1BankAmountOnly:
    @pytest.fixture(scope="class", autouse=True)
    def setup(self, request):
        s, _ = _new_client()
        request.cls.s = s
        yield
        _teardown(s)

    CSV = (
        "Date,Description,Amount\n"
        "01/09/2025,SWIGGY ORDER BLR,450\n"
        "02/09/2025,BIGBASKET GROCERY,2200\n"
        "05/09/2025,Salary from Acme,80000\n"
    )

    def test_debits_expense_income_row_income(self):
        txns, _ = _import(self.s, self.CSV, "bank")
        by_desc = {t["description"]: t for t in txns}
        assert len(txns) == 3, txns
        swiggy = by_desc["SWIGGY ORDER BLR"]
        bb = by_desc["BIGBASKET GROCERY"]
        sal = by_desc["Salary from Acme"]
        assert swiggy["type"] == "Expense", swiggy
        assert swiggy["category"] == "Food", swiggy
        assert bb["type"] == "Expense", bb
        # app taxonomy maps grocery/bigbasket -> Shopping (no separate Groceries category)
        assert bb["category"] in ("Groceries", "Shopping"), bb
        assert sal["type"] == "Income", sal
        assert swiggy["amount"] == 450.0 and bb["amount"] == 2200.0

    def test_overview_reflects_expense_totals(self):
        ov = self.s.get(f"{API}/financial/overview", timeout=60)
        assert ov.status_code == 200, ov.text[:300]
        d = ov.json()
        summary = d["summary"]
        assert summary["expenses"] == 2650, summary
        assert summary["income"] == 80000, summary
        assert all("_id" not in t for t in d["transactions"])


# ---------------- FIX 2a: conflicting refs must not auto-merge ----------------
class TestFix2aRefVeto:
    @pytest.fixture(scope="class", autouse=True)
    def setup(self, request):
        s, _ = _new_client()
        request.cls.s = s
        yield
        _teardown(s)

    def test_distinct_refs_both_survive(self):
        bank = "Date,Narration,Debit,Credit\n10/09/2025,UPI/ACME/111111111111,1250,\n"
        upi = ("Transaction Time,To / From,Amount,Type,UPI Ref No\n"
               "12 Sep 2025,ACME,1250,Sent,222222222222\n")
        _import(self.s, bank, "bank")
        _, conf = _import(self.s, upi, "upi")
        assert conf.get("merged", 0) == 0, f"conflicting refs were merged: {conf}"
        ov = self.s.get(f"{API}/financial/overview", timeout=60).json()
        rows = [t for t in ov["transactions"] if t["amount"] == 1250.0]
        assert len(rows) == 2, rows
        assert {r["source"] for r in rows} == {"bank", "upi"}, rows
        assert ov["summary"]["expenses"] == 2500, ov["summary"]


# ---------------- FIX 2b: generic token vs real merchant match ----------------
class TestFix2bTokenOverlap:
    @pytest.fixture(scope="class", autouse=True)
    def setup(self, request):
        s, _ = _new_client()
        request.cls.s = s
        yield
        _teardown(s)

    def test_generic_pair_kept_as_possible(self):
        bank = "Date,Narration,Debit,Credit\n10/09/2025,Paid Wwww Alpha,8765,\n"
        upi = ("Transaction Time,To / From,Amount,Type\n"
               "12 Sep 2025,Paid Yyyy Beta,8765,Sent\n")
        _import(self.s, bank, "bank")
        _, conf = _import(self.s, upi, "upi")
        assert conf.get("merged", 0) == 0, f"unrelated rows auto-merged: {conf}"
        ov = self.s.get(f"{API}/financial/overview", timeout=60).json()
        rows = [t for t in ov["transactions"] if t["amount"] == 8765.0]
        assert len(rows) == 2, rows
        # verify endpoint should surface it as a 'possible' duplicate, not verified
        v = self.s.get(f"{API}/statements/verify", timeout=60)
        assert v.status_code == 200, v.text[:300]
        vd = v.json()
        poss = json.dumps(vd.get("possible_matches", vd))
        assert "8765" in poss, f"pair not surfaced as possible match: {vd}"

    def test_real_merchant_match_still_dedupes(self):
        s2, _ = _new_client()
        try:
            bank = "Date,Narration,Debit,Credit\n10/09/2025,UPI/SWIGGY ORDER BLR/778899001122,450,\n"
            upi = ("Transaction Time,To / From,Amount,Type,UPI Ref No\n"
                   "10 Sep 2025,Swiggy,450,Sent,778899001122\n")
            _import(s2, bank, "bank")
            _, conf = _import(s2, upi, "upi")
            assert conf.get("merged", 0) == 1, f"genuine duplicate not merged: {conf}"
            ov = s2.get(f"{API}/financial/overview", timeout=60).json()
            rows = [t for t in ov["transactions"] if t["amount"] == 450.0]
            assert len(rows) == 1, rows
            assert ov["summary"]["expenses"] == 450, ov["summary"]
        finally:
            _teardown(s2)

    def test_merchant_in_description_only_still_dedupes(self):
        """No merchant column: UPI description tokens must still match bank narration."""
        s3, _ = _new_client()
        try:
            bank = "Date,Narration,Debit,Credit\n10/09/2025,UPI/SWIGGY ORDER BLR/778899001133,610,\n"
            upi = ("Date,Description,Amount,UPI Ref No\n"
                   "10 Sep 2025,Swiggy,610,778899001133\n")
            _import(s3, bank, "bank")
            _, conf = _import(s3, upi, "upi")
            assert conf.get("merged", 0) == 1, f"description-only duplicate not merged: {conf}"
        finally:
            _teardown(s3)


# ---------------- FIX 3: DELETE /financial/data purges memories ----------------
class TestFix3MemoryPurge:
    def test_memories_purged_and_export_clean(self):
        s, _ = _new_client()
        try:
            r = s.post(f"{API}/memories",
                       json={"category": "income", "key": "TEST_it19_key", "value": "x"}, timeout=60)
            assert r.status_code in (200, 201), r.text[:300]
            mem = s.get(f"{API}/memories", timeout=60).json()
            assert json.dumps(mem).count("TEST_it19_key") >= 1, mem
            d = s.delete(f"{API}/financial/data", timeout=60)
            assert d.status_code == 200, d.text[:300]
            after = s.get(f"{API}/memories", timeout=60).json()
            items = after if isinstance(after, list) else after.get("memories", after)
            assert not items or "TEST_it19_key" not in json.dumps(items), after
            exp = s.get(f"{API}/settings/export", timeout=60)
            assert exp.status_code == 200, exp.text[:300]
            ed = exp.json()
            assert not ed.get("memories"), ed.get("memories")
        finally:
            _teardown(s)


# ---------------- FIX 4: cr/dr word-boundary column mapping ----------------
class TestFix4ColumnMapping:
    @pytest.fixture(scope="class", autouse=True)
    def setup(self, request):
        s, _ = _new_client()
        request.cls.s = s
        yield
        _teardown(s)

    def test_description_not_mapped_to_credit(self):
        csv = "Date,Description,Debit,Balance\n10/09/2025,ATM WITHDRAWAL,500,10000\n"
        r = self.s.post(f"{API}/statements/preview", files=_files(csv),
                        data={"source": "bank"}, timeout=60)
        assert r.status_code == 200, r.text[:300]
        guess = r.json()["guess"]
        assert guess.get("credit") != "Description", guess
        assert guess.get("credit") in (None, "", "Balance") or guess.get("credit") is None, guess
        assert guess["debit"] == "Debit", guess
        assert guess["description"] == "Description", guess

    def test_explicit_cr_dr_columns_still_map(self):
        csv = "Txn Date,Particulars,DR,CR\n10/09/2025,SHOP,750,\n"
        r = self.s.post(f"{API}/statements/preview", files=_files(csv),
                        data={"source": "bank"}, timeout=60)
        assert r.status_code == 200, r.text[:300]
        guess = r.json()["guess"]
        assert guess["debit"] == "DR", guess
        assert guess["credit"] == "CR", guess
