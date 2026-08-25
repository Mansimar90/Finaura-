"""Iteration 22 — real bank PDF format regressions.

Tests the numbered-row bank parser (Strategy 1.7) against two representative
layouts confirmed to work end-to-end on real customer statements:
* BoB style — bilingual Hindi/English merged header + Debit/Credit columns
  with '-' placeholders per row, description wraps ABOVE and BELOW the row.
* SBI style — single amount column per row (direction inferred from balance
  delta), merchant name prefix on the line right before the row, UPI trail
  wrapped underneath."""
import io
import os
import uuid
import requests
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet

BASE = os.environ.get("FINAURA_API_BASE", "http://localhost:8001")
API = f"{BASE}/api"


def _build_bob_pdf() -> bytes:
    """BoB layout: bilingual header, per-row 'Sr Date ValueDate Debit Credit Balance'
    with '-' for the empty side; description text wraps above and below the row."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4)
    styles = getSampleStyleSheet()
    elems = [
        Paragraph("Account Statement from 25-02-2026 to 25-05-2026", styles["Normal"]),
        Paragraph("MANSIMAR SINGH BAKSHI  Account Number 27170100014128", styles["Normal"]),
        Paragraph("Sr.No Transaction Date Value Date Description Cheque Debit Credit Balance", styles["Normal"]),
        Spacer(1, 6),
        Paragraph("1 25-02-2026 Opening Balance - - 2,056.35", styles["Normal"]),
        Paragraph("UPI/397916382823/15:25:26/UPI/amznplmcdc000210", styles["Normal"]),
        Paragraph("2 25-02-2026 25-02-2026 31.50 - 2,024.85", styles["Normal"]),
        Paragraph("@ap", styles["Normal"]),
        Paragraph("UPI/397937588719/20:17:50/UPI/q303458705@ybl/Se", styles["Normal"]),
        Paragraph("3 25-02-2026 25-02-2026 300.00 - 1,724.85", styles["Normal"]),
        Paragraph("nt", styles["Normal"]),
        Paragraph("UPI/606234711723/19:25:44/UPI/9212733316@pthdfc", styles["Normal"]),
        Paragraph("4 03-03-2026 03-03-2026 - 975.00 1,851.85", styles["Normal"]),
        Paragraph("/N", styles["Normal"]),
        Paragraph("UPI/260302060909/16:04:29/UPI/8383934224@axl/Pa", styles["Normal"]),
        Paragraph("5 06-03-2026 06-03-2026 - 850.00 2,561.85", styles["Normal"]),
        Paragraph("ym", styles["Normal"]),
    ]
    doc.build(elems)
    return buf.getvalue()


def _build_sbi_pdf() -> bytes:
    """SBI layout: single amount column, merchant name on the line BEFORE the row,
    UPI trail wrapped underneath, direction inferred from balance delta."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4)
    styles = getSampleStyleSheet()
    elems = [
        Paragraph("Statement of Transactions in Saving Account", styles["Normal"]),
        Paragraph("PRABHKIRAT SINGH", styles["Normal"]),
        Paragraph("S No. Transaction Date Cheque Number Transaction Remarks Withdrawal Deposit Balance", styles["Normal"]),
        Spacer(1, 6),
        Paragraph("GURWINDER", styles["Normal"]),
        Paragraph("1 28.02.2026 500.00 522.86", styles["Normal"]),  # opening balance was 1022.86, this is a debit
        Paragraph("UPI/GURWINDER/8508700009@pty/Sent from/ICICI", styles["Normal"]),
        Paragraph("Bank/200980628710/PTM13413A1D1A1E4E89A90", styles["Normal"]),
        Paragraph("ZOMATO", styles["Normal"]),
        Paragraph("2 28.02.2026 295.83 227.03", styles["Normal"]),  # debit — balance went down
        Paragraph("UPI/ZOMATO/payzomato@hdfc/Payment fr/HDFC", styles["Normal"]),
        Paragraph("BANK/281638621357", styles["Normal"]),
        Paragraph("RUPINDER K", styles["Normal"]),
        Paragraph("3 03.03.2026 8000.00 8227.03", styles["Normal"]),  # credit — balance jumped up
        Paragraph("UPI/RUPINDER K/8437049955-2@a/Payment", styles["Normal"]),
        Paragraph("fr/Punjab Nat/372459624698", styles["Normal"]),
        Paragraph("RAUNQMEDIC", styles["Normal"]),
        Paragraph("4 03.03.2026 240.00 7987.03", styles["Normal"]),  # debit
        Paragraph("UPI/RAUNQMEDIC/paytmqr1egc56s/Payment", styles["Normal"]),
        Paragraph("fr/YES BANK L/235490893762", styles["Normal"]),
    ]
    doc.build(elems)
    return buf.getvalue()


def _register(label: str):
    email = f"it22_{label}_{uuid.uuid4().hex[:8]}@qa.finaura.dev"
    s = requests.Session()
    r = s.post(f"{API}/auth/register",
               json={"email": email, "password": "testpass123", "name": label},
               timeout=30)
    assert r.status_code == 200, r.text[:300]
    s.headers.update({"Authorization": f"Bearer {r.json()['access_token']}"})
    s.post(f"{API}/auth/onboard", json={"start_with": "blank"}, timeout=30)
    return s


class TestBobFormat:
    def test_bob_debit_credit_columns_direction(self):
        s = _register("bob")
        r = s.post(f"{API}/statements/preview",
                   files={"file": ("bob.pdf", _build_bob_pdf(), "application/pdf")},
                   data={"source": "bank"}, timeout=60)
        assert r.status_code == 200
        d = r.json()
        summary = d["extraction_summary"]
        # 4 real transactions (opening balance row rejected: no debit or credit)
        assert summary["transactions_detected"] == 4, d
        assert summary["debits_count"] == 2, summary  # sr 2 (31.50) + sr 3 (300)
        assert summary["credits_count"] == 2, summary  # sr 4 (975) + sr 5 (850)
        assert summary["debits_total"] == 331.5, summary
        assert summary["credits_total"] == 1825.0, summary
        # Every txn must have a UPI ref extracted
        for t in d["transactions"]:
            assert t.get("upi_ref"), t


class TestSbiFormat:
    def test_sbi_single_amount_balance_delta_direction(self):
        s = _register("sbi")
        r = s.post(f"{API}/statements/preview",
                   files={"file": ("sbi.pdf", _build_sbi_pdf(), "application/pdf")},
                   data={"source": "bank"}, timeout=60)
        assert r.status_code == 200
        d = r.json()
        summary = d["extraction_summary"]
        assert summary["transactions_detected"] == 4, d
        # Row 3 (8000 with balance jumping from 227.03 to 8227.03) must be Income;
        # every other row must be Expense (balance dropped).
        by_amt = {t["amount"]: t for t in d["transactions"]}
        assert by_amt[500.0]["type"] == "Expense"
        assert by_amt[295.83]["type"] == "Expense"
        assert by_amt[8000.0]["type"] == "Income"
        assert by_amt[240.0]["type"] == "Expense"
        # Merchants from the name-prefix line
        assert "GURWINDER" in by_amt[500.0]["description"]
        assert "ZOMATO" in by_amt[295.83]["description"]
        assert "RUPINDER" in by_amt[8000.0]["description"]
        assert "RAUNQMEDIC" in by_amt[240.0]["description"]

    def test_sbi_descriptions_do_not_bleed_across_rows(self):
        s = _register("sbi2")
        r = s.post(f"{API}/statements/preview",
                   files={"file": ("sbi.pdf", _build_sbi_pdf(), "application/pdf")},
                   data={"source": "bank"}, timeout=60)
        d = r.json()
        by_amt = {t["amount"]: t for t in d["transactions"]}
        # Row 2 (ZOMATO) must NOT contain 'GURWINDER' or 'PTM13413'
        z = by_amt[295.83]["description"]
        assert "GURWINDER" not in z, z
        assert "PTM13413" not in z, z
        # Row 3 (RUPINDER) must NOT contain 'ZOMATO'
        r_desc = by_amt[8000.0]["description"]
        assert "ZOMATO" not in r_desc, r_desc
