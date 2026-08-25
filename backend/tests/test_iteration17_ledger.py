"""Iteration 17 — unified deduped ledger, credit/debit classification, demo isolation,
statement list + per-statement delete.

Modules covered:
  - POST /api/statements/preview + /parse  (bank + upi CSV)
  - POST /api/statements/confirm-import    (dedupe merge across sources, demo purge)
  - POST /api/statements/import-demo       (409 when real data exists)
  - GET  /api/financial/overview           (no double count, real-only analytics)
  - GET  /api/statements/list              (grouped statements)
  - DELETE /api/statements/{id}            (scoped delete, 404 for foreign id)
  - statements.guess_category / resolve_type_and_category (unit)
"""
import os
import sys
import uuid

import pytest
import requests
from dotenv import dotenv_values

sys.path.insert(0, "/app/backend")
from statements import guess_category, resolve_type_and_category, parse_csv  # noqa: E402

frontend_env = dotenv_values("/app/frontend/.env")
base_url = os.environ.get("REACT_APP_BACKEND_URL") or frontend_env.get("REACT_APP_BACKEND_URL")
if not base_url:
    raise RuntimeError("REACT_APP_BACKEND_URL missing")
BASE_URL = base_url.rstrip("/")
API = f"{BASE_URL}/api"

BANK_CSV = (
    "Date,Description,Debit,Credit\n"
    "10/08/2025,SWIGGY ORDER BLR,450,\n"
    "05/08/2025,SALARY CREDIT ACME,,80000\n"
    "12/08/2025,ATM WITHDRAWAL BLR,5000,\n"
    "15/08/2025,MYSTERY VENDOR 87654,3000,\n"
    "18/08/2025,Self transfer to own account,25000,\n"
)

# Realistic Indian bank narration for a UPI payment (contains 'UPI' + the UPI ref)
BANK_CSV_UPI_NARRATION = (
    "Date,Description,Debit,Credit\n"
    "10/08/2025,UPI/SWIGGY/412345678901/Food order,450,\n"
    "05/08/2025,SALARY CREDIT ACME,,80000\n"
    "12/08/2025,ATM WITHDRAWAL BLR,5000,\n"
)

UPI_CSV = (
    "Date,Description,Amount,UPI Ref\n"
    "12/08/2025,Swiggy,450,412345678901\n"
    "20/08/2025,Salary from Acme Inc,80000,412345678902\n"
    "21/08/2025,Refund from Nykaa,1200,412345678903\n"
    "22/08/2025,Amazon Pay,2500,412345678904\n"
)


# ---------------- helpers ----------------
def _new_user(suffix=""):
    email = f"it17_{suffix}{uuid.uuid4().hex[:8]}@qa.finaura.dev"
    s = requests.Session()
    r = s.post(f"{API}/auth/register", json={"email": email, "password": "testpass123", "name": "IT17"}, timeout=40)
    assert r.status_code == 200, f"register failed {r.status_code}: {r.text[:300]}"
    token = r.json().get("token") or r.json().get("access_token")
    assert token, r.text[:300]
    s.headers.update({"Authorization": f"Bearer {token}"})
    s.email = email
    return s


def _preview(client, csv_text, source, filename):
    files = {"file": (filename, csv_text.encode(), "text/csv")}
    r = client.post(f"{API}/statements/preview", files=files, data={"source": source}, timeout=60)
    assert r.status_code == 200, f"preview {source} failed {r.status_code}: {r.text[:400]}"
    return r.json()


def _parse(client, csv_text, source, filename):
    import json as _json
    mapping = _preview(client, csv_text, source, filename).get("guess") or {}
    files = {"file": (filename, csv_text.encode(), "text/csv")}
    r = client.post(f"{API}/statements/parse", files=files,
                    data={"mapping": _json.dumps(mapping), "source": source}, timeout=60)
    assert r.status_code == 200, f"parse {source} failed {r.status_code}: {r.text[:400]}"
    txns = r.json()["transactions"]
    assert txns, "no transactions parsed"
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


# ---------------- unit: classification ----------------
class TestClassificationUnits:
    def test_unknown_debit_is_misc_debit(self):
        assert guess_category("MYSTERY VENDOR 87654", "Expense") == "Miscellaneous Debit"

    def test_unknown_credit_is_misc_credit(self):
        assert guess_category("RANDOM INFLOW XYZ", "Income") == "Miscellaneous Credit"

    def test_internal_transfer_detected(self):
        t, c = resolve_type_and_category("Self transfer to own account", "Expense", None)
        assert c == "Internal Transfer" and t == "Expense"

    def test_internal_transfer_credit_not_income(self):
        t, c = resolve_type_and_category("Self transfer from own account", "Income", None)
        assert c == "Internal Transfer"

    def test_salary_credit_is_income(self):
        assert guess_category("SALARY CREDIT ACME", "Income") == "Income"

    def test_parse_csv_bank_debit_never_income(self):
        mapping = {"date": "Date", "description": "Description", "debit": "Debit", "credit": "Credit"}
        rows = parse_csv(BANK_CSV.encode(), mapping, source="bank")
        by_desc = {r["description"].upper(): r for r in rows}
        for key in ("ATM WITHDRAWAL BLR", "MYSTERY VENDOR 87654", "SWIGGY ORDER BLR"):
            assert by_desc[key]["type"] == "Expense", f"{key} -> {by_desc[key]}"


# ---------------- bank classification through API ----------------
class TestBankClassification:
    @pytest.fixture(scope="class")
    def client(self):
        c = _new_user("bank")
        yield c
        _cleanup(c)

    def test_bank_import_classification(self, client):
        txns = _parse(client, BANK_CSV, "bank", "bank_aug.csv")
        res = _import(client, txns, "bank", "bank_aug.csv")
        assert res["imported"] == 5, res
        assert res["merged"] == 0, res

        ov = _overview(client)
        rows = {t["description"].upper(): t for t in ov["transactions"]}
        assert rows["ATM WITHDRAWAL BLR"]["type"] == "Expense"
        assert rows["ATM WITHDRAWAL BLR"]["category"] == "Miscellaneous Debit"
        assert rows["MYSTERY VENDOR 87654"]["type"] == "Expense"
        assert rows["MYSTERY VENDOR 87654"]["category"] == "Miscellaneous Debit"
        assert rows["SALARY CREDIT ACME"]["type"] == "Income"
        assert rows["SALARY CREDIT ACME"]["category"] == "Income"
        assert rows["SELF TRANSFER TO OWN ACCOUNT"]["type"] == "Expense"
        assert rows["SELF TRANSFER TO OWN ACCOUNT"]["category"] == "Internal Transfer"
        # no debit became income
        assert all(t["type"] == "Expense" for d, t in rows.items() if d != "SALARY CREDIT ACME")
        # no mongo internals leaked
        assert all("_id" not in t for t in ov["transactions"])


# ---------------- upi classification ----------------
class TestUpiClassification:
    @pytest.fixture(scope="class")
    def client(self):
        c = _new_user("upi")
        yield c
        _cleanup(c)

    def test_upi_import_classification(self, client):
        txns = _parse(client, UPI_CSV, "upi", "upi_aug.csv")
        res = _import(client, txns, "upi", "upi_aug.csv")
        assert res["imported"] == 4, res
        ov = _overview(client)
        rows = {t["description"].upper(): t for t in ov["transactions"]}
        assert rows["SALARY FROM ACME INC"]["type"] == "Income"
        assert rows["SALARY FROM ACME INC"]["category"] == "Income"
        assert rows["REFUND FROM NYKAA"]["type"] == "Income"
        assert rows["REFUND FROM NYKAA"]["category"] == "Miscellaneous Credit"
        assert rows["SWIGGY"]["type"] == "Expense"
        assert rows["SWIGGY"]["category"] == "Food"
        assert rows["AMAZON PAY"]["type"] == "Expense"
        assert rows["AMAZON PAY"]["category"] == "Shopping"


# ---------------- dedupe: bank then upi ----------------
class TestDedupeBankThenUpi:
    @pytest.fixture(scope="class")
    def client(self):
        c = _new_user("b2u")
        yield c
        _cleanup(c)

    def test_bank_then_upi_merges_once(self, client):
        bank = _parse(client, BANK_CSV_UPI_NARRATION, "bank", "bank_aug.csv")
        r1 = _import(client, bank, "bank", "bank_aug.csv")
        assert r1["imported"] == 3

        upi = _parse(client, UPI_CSV, "upi", "upi_aug.csv")
        r2 = _import(client, upi, "upi", "upi_aug.csv")
        assert r2["merged"] == 1, f"expected the Swiggy 450 pair to merge: {r2}"
        assert r2["imported"] == 4

        ov = _overview(client)
        txns = ov["transactions"]
        swiggy = [t for t in txns if "SWIGGY" in t["description"].upper()]
        assert len(swiggy) == 1, f"Swiggy 450 double counted: {swiggy}"
        assert swiggy[0]["amount"] == 450

    def test_no_double_count_in_summary(self, client):
        ov = _overview(client)
        txns = ov["transactions"]
        expenses = sum(t["amount"] for t in txns if t["type"] == "Expense")
        income = sum(t["amount"] for t in txns if t["type"] == "Income")
        # Deduped expenses: swiggy 450 (once) + atm 5000 + amazon 2500
        assert expenses == 7950, f"unexpected expense total {expenses}: {txns}"
        # Bank salary 80000 (05 Aug) + UPI salary 80000 (20 Aug) -> 15 days apart, both real
        assert income == 161200, f"unexpected income total {income}"
        assert ov["has_real_data"] is True
        assert ov["has_demo_data"] is False


class TestDedupePlainNarration:
    """Same payment on both statements but the bank narration has no 'UPI' token and no
    UPI ref (e.g. 'SWIGGY ORDER BLR' vs 'Swiggy', 2-day settlement lag)."""

    @pytest.fixture(scope="class")
    def client(self):
        c = _new_user("plain")
        yield c
        _cleanup(c)

    def test_plain_narration_pair_is_deduped(self, client):
        bank = _parse(client, BANK_CSV, "bank", "bank_aug.csv")
        _import(client, bank, "bank", "bank_aug.csv")
        upi = _parse(client, UPI_CSV, "upi", "upi_aug.csv")
        r2 = _import(client, upi, "upi", "upi_aug.csv")
        ov = _overview(client)
        swiggy = [t for t in ov["transactions"] if "SWIGGY" in t["description"].upper()]
        expenses = sum(t["amount"] for t in ov["transactions"] if t["type"] == "Expense")
        assert len(swiggy) == 1, (
            f"Swiggy 450 counted twice (merged={r2['merged']}, expenses={expenses}); "
            f"match score for plain narration + 2-day lag is 0.75 < 0.85 threshold"
        )


# ---------------- dedupe: upi then bank ----------------
class TestDedupeUpiThenBank:
    @pytest.fixture(scope="class")
    def client(self):
        c = _new_user("u2b")
        yield c
        _cleanup(c)

    def test_upi_then_bank_merges_once(self, client):
        upi = _parse(client, UPI_CSV, "upi", "upi_aug.csv")
        _import(client, upi, "upi", "upi_aug.csv")
        bank = _parse(client, BANK_CSV_UPI_NARRATION, "bank", "bank_aug.csv")
        r2 = _import(client, bank, "bank", "bank_aug.csv")
        assert r2["merged"] == 1, f"expected merge on reverse order: {r2}"

        ov = _overview(client)
        swiggy = [t for t in ov["transactions"] if "SWIGGY" in t["description"].upper()]
        assert len(swiggy) == 1, f"Swiggy double counted in reverse order: {swiggy}"


# ---------------- demo isolation ----------------
class TestDemoIsolation:
    @pytest.fixture(scope="class")
    def client(self):
        c = _new_user("demo")
        yield c
        _cleanup(c)

    def test_demo_then_real_purges_demo(self, client):
        r = client.post(f"{API}/statements/import-demo", timeout=60)
        assert r.status_code == 200, r.text[:300]
        ov = _overview(client)
        demo_count = len(ov["transactions"])
        assert demo_count > 0

        bank = _parse(client, BANK_CSV, "bank", "bank_aug.csv")
        _import(client, bank, "bank", "bank_aug.csv")
        ov2 = _overview(client)
        sources = {t.get("source") for t in ov2["transactions"]}
        assert "demo" not in sources, f"demo rows still mixed: {sources}"
        assert len(ov2["transactions"]) == 5, f"expected only 5 real rows, got {len(ov2['transactions'])}"
        assert ov2["has_demo_data"] is False
        assert ov2["has_real_data"] is True

    def test_demo_import_after_real_is_409(self, client):
        r = client.post(f"{API}/statements/import-demo", timeout=60)
        assert r.status_code == 409, f"expected 409, got {r.status_code}: {r.text[:300]}"


# ---------------- statements list + delete ----------------
class TestStatementListDelete:
    @pytest.fixture(scope="class")
    def client(self):
        c = _new_user("del")
        yield c
        _cleanup(c)

    @pytest.fixture(scope="class")
    def imported(self, client):
        bank = _parse(client, BANK_CSV_UPI_NARRATION, "bank", "bank_aug.csv")
        b = _import(client, bank, "bank", "bank_aug.csv")
        upi = _parse(client, UPI_CSV, "upi", "upi_aug.csv")
        u = _import(client, upi, "upi", "upi_aug.csv")
        assert u["merged"] == 1, f"setup expected a merge: {u}"
        return {"bank": b, "upi": u}

    def test_list_statements(self, client, imported):
        r = client.get(f"{API}/statements/list", timeout=30)
        assert r.status_code == 200, r.text[:300]
        data = r.json()
        assert len(data) == 2, data
        for s in data:
            for key in ("statement_id", "source", "count", "total_income", "total_expenses",
                        "first_date", "last_date", "imported_at", "file_name"):
                assert key in s, f"missing {key} in {s}"
            assert s["count"] > 0
        by_src = {s["source"]: s for s in data}
        assert by_src["bank"]["count"] == 3
        assert by_src["bank"]["total_income"] == 80000
        assert by_src["upi"]["count"] == 4

    def test_delete_foreign_statement_404(self, client, imported):
        r = client.delete(f"{API}/statements/{uuid.uuid4()}", timeout=30)
        assert r.status_code == 404, f"expected 404 got {r.status_code}: {r.text[:200]}"

    def test_delete_statement_scoped(self, client, imported):
        upi_sid = imported["upi"]["statement_id"]
        r = client.delete(f"{API}/statements/{upi_sid}", timeout=30)
        assert r.status_code == 200, r.text[:300]
        assert r.json()["deleted"] == 4, r.json()

        lst = client.get(f"{API}/statements/list", timeout=30).json()
        assert len(lst) == 1 and lst[0]["source"] == "bank", lst

        ov = _overview(client)
        assert len(ov["transactions"]) == 3, f"bank rows should survive: {len(ov['transactions'])}"
        # linkage cleaned up on the surviving twin
        swiggy = [t for t in ov["transactions"] if "SWIGGY" in t["description"].upper()]
        assert len(swiggy) == 1
        assert not swiggy[0].get("linked_txn_id"), f"stale linkage: {swiggy[0]}"
        assert not swiggy[0].get("verified"), f"stale verified flag: {swiggy[0]}"

    def test_delete_last_statement_leaves_empty_ledger(self, client, imported):
        bank_sid = imported["bank"]["statement_id"]
        r = client.delete(f"{API}/statements/{bank_sid}", timeout=30)
        assert r.status_code == 200
        assert client.get(f"{API}/statements/list", timeout=30).json() == []
        ov = _overview(client)
        assert ov["transactions"] == []
        assert ov["has_real_data"] is False


# ---------------- regression: verify endpoint still works ----------------
class TestVerifyRegression:
    @pytest.fixture(scope="class")
    def client(self):
        c = _new_user("ver")
        bank = _parse(c, BANK_CSV, "bank", "bank_aug.csv")
        _import(c, bank, "bank", "bank_aug.csv")
        upi = _parse(c, UPI_CSV, "upi", "upi_aug.csv")
        _import(c, upi, "upi", "upi_aug.csv")
        yield c
        _cleanup(c)

    def test_verify_shape(self, client):
        r = client.get(f"{API}/statements/verify", timeout=30)
        assert r.status_code == 200, r.text[:300]
        data = r.json()
        for key in ("verified_matches", "possible_matches", "upi_only", "bank_only", "counts"):
            assert key in data, f"missing {key}: {list(data.keys())}"
        for key in ("verified", "possible", "upi_only", "bank_only"):
            assert key in data["counts"], f"missing counts.{key}"
        assert "months" in data
        assert "matched_bank_ids" not in data and "verified_bank_ids" not in data

    def test_preview_both_sources(self, client):
        for csv_text, src in ((BANK_CSV, "bank"), (UPI_CSV, "upi")):
            files = {"file": (f"{src}.csv", csv_text.encode(), "text/csv")}
            r = client.post(f"{API}/statements/preview", files=files, data={"source": src}, timeout=40)
            assert r.status_code == 200, f"preview {src}: {r.status_code} {r.text[:200]}"
            body = r.json()
            assert body["kind"] == "csv" and body["source"] == src
            assert body.get("columns") or body.get("mapping")
