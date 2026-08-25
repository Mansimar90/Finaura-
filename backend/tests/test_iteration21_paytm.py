"""Iteration 21 — Paytm-style multi-line record parser.

Regression coverage for the real-world Paytm UPI PDF export where each
transaction spans 4-5 physical lines with a signed `- Rs.NN` / `+ Rs.NN`
amount at the end of the header line. Uses reportlab to synthesize a PDF
whose text layer looks like the actual Paytm 47-page statement."""
import io
import os
import uuid
import pytest
import requests

from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet

BASE = os.environ.get("FINAURA_API_BASE", "http://localhost:8001")
API = f"{BASE}/api"


def _build_paytm_style_pdf() -> bytes:
    """Emit a PDF whose text layer, when pdfplumber-extracted, looks like Paytm:
    a mix of header lines that START with 'DD Mon' and END with '[+-] Rs.NN',
    followed by wrap continuations with UPI ID / UPI Ref No / Order ID."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4)
    styles = getSampleStyleSheet()
    elems = [
        Paragraph("Paytm User", styles["Normal"]),
        Paragraph("Paytm Statement for Total Money Paid Total Money Received", styles["Normal"]),
        Paragraph("25 FEB'26 - 24 AUG'26 - Rs.5,000 + Rs.6,500", styles["Normal"]),
        Paragraph("235 Payments made 74 Payments received", styles["Normal"]),
        Spacer(1, 8),
        Paragraph("Passbook Payments History", styles["Normal"]),
        Paragraph("Date & Transaction Details Notes & Tags Your Account Amount", styles["Normal"]),
        Paragraph("Time", styles["Normal"]),
        # Record 1 — Received (income)
        Paragraph("24 Aug Received from Gurneet Kour Kohli Tag: Bank Of + Rs.500", styles["Normal"]),
        Paragraph("9:34 PM", styles["Normal"]),
        Paragraph("UPI ID: gurneetkohli908@okicici on # Money Received Baroda - 28", styles["Normal"]),
        Paragraph("UPI Ref No: 623608897569", styles["Normal"]),
        # Record 2 — Paid to (expense with Note field)
        Paragraph("23 Aug Paid to Meesho Note: UPI Intent Bank Of - Rs.265", styles["Normal"]),
        Paragraph("10:09 AM", styles["Normal"]),
        Paragraph("UPI ID: paytm-17731298@ptybl on Tag: Baroda - 28", styles["Normal"]),
        Paragraph("# Shopping", styles["Normal"]),
        Paragraph("UPI Ref No: 623573177942", styles["Normal"]),
        # Record 3 — Paid to (expense, another merchant)
        Paragraph("22 Aug Paid to Manish Kumar Tag: Bank Of - Rs.50", styles["Normal"]),
        Paragraph("6:37 PM", styles["Normal"]),
        Paragraph("UPI ID: q067870999@ybl on # Groceries Baroda - 28", styles["Normal"]),
        Paragraph("UPI Ref No: 623483206589", styles["Normal"]),
        # Record 4 — Money sent (expense)
        Paragraph("19 Aug Money sent to Prabhkirat Singh Tag: Bank Of - Rs.890", styles["Normal"]),
        Paragraph("11:37 PM", styles["Normal"]),
        Paragraph("UPI ID: prabhkirat099@icici # Money Transfer Baroda - 28", styles["Normal"]),
        Paragraph("UPI Ref No: 312829588969", styles["Normal"]),
        # Record 5 — Received (income, salary-like)
        Paragraph("17 Aug Received from ACME Corp Tag: Bank Of + Rs.2,000", styles["Normal"]),
        Paragraph("9:00 AM", styles["Normal"]),
        Paragraph("UPI ID: acmecorp@ptyes # Money Received Baroda - 28", styles["Normal"]),
        Paragraph("UPI Ref No: 312627764380", styles["Normal"]),
        Paragraph("Page 1 of 1 For any queries, Contact Us", styles["Normal"]),
    ]
    doc.build(elems)
    return buf.getvalue()


def _register():
    email = f"it21_{uuid.uuid4().hex[:8]}@qa.finaura.dev"
    s = requests.Session()
    r = s.post(f"{API}/auth/register",
               json={"email": email, "password": "testpass123", "name": "IT21"},
               timeout=30)
    assert r.status_code == 200, r.text[:300]
    s.headers.update({"Authorization": f"Bearer {r.json()['access_token']}"})
    s.post(f"{API}/auth/onboard", json={"start_with": "blank"}, timeout=30)
    return s


class TestPaytmRecordParser:
    def test_paytm_multiline_pdf_extracts_all_records(self):
        s = _register()
        pdf = _build_paytm_style_pdf()
        r = s.post(f"{API}/statements/preview",
                   files={"file": ("paytm.pdf", pdf, "application/pdf")},
                   data={"source": "upi"}, timeout=60)
        assert r.status_code == 200, r.text[:400]
        d = r.json()
        assert d["kind"] == "pdf"
        summary = d.get("extraction_summary") or {}
        # 5 records in the synthetic PDF; statement header line must NOT be counted
        assert summary["transactions_detected"] == 5, summary
        assert summary["credits_count"] == 2, summary
        assert summary["debits_count"] == 3, summary
        # Amounts must match exactly
        assert summary["credits_total"] == 2500.0, summary  # 500 + 2000
        assert summary["debits_total"] == 1205.0, summary  # 265 + 50 + 890

        # Merchants and descriptions must be readable (not "UPI payment")
        txns = d["transactions"]
        by_amt = {t["amount"]: t for t in txns}
        assert "Gurneet" in (by_amt[500.0].get("description", "") + by_amt[500.0].get("merchant", ""))
        assert by_amt[500.0]["type"] == "Income"
        assert by_amt[265.0].get("merchant") == "Meesho"
        assert by_amt[265.0]["type"] == "Expense"
        assert by_amt[50.0].get("merchant") == "Manish Kumar"
        assert by_amt[890.0].get("merchant") == "Prabhkirat Singh"
        assert by_amt[2000.0].get("merchant") == "ACME Corp"

        # UPI ref must be captured
        assert by_amt[500.0].get("upi_ref") == "623608897569"
        assert by_amt[265.0].get("upi_ref") == "623573177942"

    def test_statement_period_header_is_not_a_transaction(self):
        """The '25 FEB'26 - 24 AUG'26 - Rs.5,000 + Rs.6,500' summary header must
        never surface as a Rs.5000 or Rs.6500 transaction."""
        s = _register()
        pdf = _build_paytm_style_pdf()
        r = s.post(f"{API}/statements/preview",
                   files={"file": ("paytm.pdf", pdf, "application/pdf")},
                   data={"source": "upi"}, timeout=60)
        d = r.json()
        amounts = {t["amount"] for t in d["transactions"]}
        assert 5000 not in amounts and 6500 not in amounts, amounts
        assert 500 in amounts and 2000 in amounts  # real income
