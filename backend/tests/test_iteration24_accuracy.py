"""Iteration 24 — accuracy verification pipeline.

Covers the accuracy-first requirements:
* Declared totals extracted from the statement header (Paytm, GPay).
* Balance-reconciliation on bank rows with a running balance.
* `unified_source: bank | upi | both` on the master ledger.
* `verification_pass` flag flips to False and a warning is surfaced when
  parsed totals diverge from the statement's own declared totals by > 1 %.
"""
import io
import os
import uuid
import requests
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4

BASE = os.environ.get("FINAURA_API_BASE", "http://localhost:8001")
API = f"{BASE}/api"


def _draw(lines: list[str], font: str = "Helvetica") -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setFont(font, 10)
    y = 800
    for ln in lines:
        c.drawString(50, y, ln)
        y -= 14
    c.save()
    return buf.getvalue()


def _draw_with_rupee(lines: list[str]) -> bytes:
    if "FreeSans" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("FreeSans", "/usr/share/fonts/truetype/freefont/FreeSans.ttf"))
    return _draw(lines, font="FreeSans")


def _register(label: str):
    email = f"it24_{label}_{uuid.uuid4().hex[:8]}@qa.finaura.dev"
    s = requests.Session()
    r = s.post(f"{API}/auth/register",
               json={"email": email, "password": "testpass123", "name": label},
               timeout=30)
    assert r.status_code == 200, r.text[:300]
    s.headers.update({"Authorization": f"Bearer {r.json()['access_token']}"})
    s.post(f"{API}/auth/onboard", json={"start_with": "blank"}, timeout=30)
    return s


class TestDeclaredTotalsPaytm:
    def test_declared_totals_extracted_and_verification_passes(self):
        s = _register("paytm_ok")
        # Paytm-style: header carries 'Rs.NN + Rs.NN' totals AND 'NN Payments made MM Payments received'
        pdf = _draw([
            "Paytm User",
            "Paytm Statement for Total Money Paid Total Money Received",
            "25 FEB'26 - 24 AUG'26 - Rs.780 + Rs.500",
            "3 Payments made 1 Payments received",
            "Passbook Payments History",
            "Date & Transaction Details Notes & Tags Your Account Amount",
            "Time",
            "24 Aug Paid to Meesho Tag: Bank Of - Rs.265",
            "10:09 AM UPI ID: paytm-1@ptybl on Tag: Baroda - 28",
            "UPI Ref No: 623573177942",
            "23 Aug Paid to Manish Kumar Tag: Bank Of - Rs.250",
            "6:37 PM UPI ID: q067@ybl on Baroda - 28",
            "UPI Ref No: 623483206589",
            "22 Aug Paid to Zomato Tag: Bank Of - Rs.265",
            "6:37 PM UPI ID: paytm-2@ptybl on Baroda - 28",
            "UPI Ref No: 623483206600",
            "17 Aug Received from ACME Corp Tag: Bank Of + Rs.500",
            "9:00 AM UPI ID: acmecorp@ptyes Baroda - 28",
            "UPI Ref No: 312627764380",
        ])
        r = s.post(f"{API}/statements/preview",
                   files={"file": ("paytm.pdf", pdf, "application/pdf")},
                   data={"source": "upi"}, timeout=60)
        assert r.status_code == 200
        su = r.json()["extraction_summary"]
        assert su["transactions_detected"] == 4, su
        # Declared totals from the header
        v = su["verification"]
        assert v["declared"]["declared_debits"] == 780.0
        assert v["declared"]["declared_credits"] == 500.0
        assert v["declared"]["declared_debit_count"] == 3
        assert v["declared"]["declared_credit_count"] == 1
        # Parsed matches declared → verification passes
        assert su["verification_pass"] is True, su
        assert su["debits_total"] == 780.0
        assert su["credits_total"] == 500.0

    def test_verification_fails_when_declared_and_parsed_diverge(self):
        s = _register("paytm_bad")
        # Header PROMISES ₹1,000 in debits but we only include ₹265 of them.
        pdf = _draw([
            "Paytm Statement for Total Money Paid Total Money Received",
            "25 FEB'26 - 24 AUG'26 - Rs.1,000 + Rs.500",
            "5 Payments made 1 Payments received",
            "Date & Transaction Details Notes & Tags Your Account Amount",
            "Time",
            "24 Aug Paid to Meesho Tag: Bank Of - Rs.265",
            "10:09 AM UPI ID: paytm-1@ptybl on Baroda - 28",
            "UPI Ref No: 623573177942",
            "17 Aug Received from ACME Corp Tag: Bank Of + Rs.500",
            "9:00 AM UPI ID: acmecorp@ptyes Baroda - 28",
            "UPI Ref No: 312627764380",
        ])
        r = s.post(f"{API}/statements/preview",
                   files={"file": ("paytm_bad.pdf", pdf, "application/pdf")},
                   data={"source": "upi"}, timeout=60)
        su = r.json()["extraction_summary"]
        assert su["verification_pass"] is False, su
        joined = " ".join(su["warnings"])
        assert "declared" in joined.lower() or "Parsed debits" in joined, su["warnings"]


class TestBalanceReconciliation:
    def test_bank_running_balance_reconciles(self):
        s = _register("recon_ok")
        pdf = _draw([
            "Account Statement from 01-01-2026 to 05-01-2026",
            "PRABHKIRAT SINGH",
            "1 01-01-2026 Opening - - 10,000.00",
            "UPI/1/12:00:00/UPI/x@ybl/Sent",
            "2 02-01-2026 02-01-2026 500.00 - 9,500.00",
            "UPI/2/13:00:00/UPI/y@ybl/Rec",
            "3 03-01-2026 03-01-2026 - 1,000.00 10,500.00",
            "UPI/3/14:00:00/UPI/z@ybl/Sent",
            "4 04-01-2026 04-01-2026 200.00 - 10,300.00",
        ])
        r = s.post(f"{API}/statements/preview",
                   files={"file": ("bank.pdf", pdf, "application/pdf")},
                   data={"source": "bank"}, timeout=60)
        su = r.json()["extraction_summary"]
        assert su["verification_pass"] is True, su
        recon = su["verification"]["reconciliation"]
        assert recon["ok"] is True, recon
        assert recon["checked"] >= 2
        assert recon["mismatches"] == 0

    def test_balance_reconciliation_mismatch_flagged(self):
        s = _register("recon_bad")
        # Row 2 balance says 9,000 → row 3 debits 500 → expected 8,500 but
        # statement lies and says 7,000 (as if a row between them is missing).
        pdf = _draw([
            "Account Statement from 01-01-2026 to 05-01-2026",
            "UPI/1/12:00:00/UPI/x@ybl/Sent",
            "1 01-01-2026 01-01-2026 - 9,000.00 9,000.00",
            "UPI/2/13:00:00/UPI/y@ybl/Sent",
            "2 02-01-2026 02-01-2026 500.00 - 7,000.00",
            "UPI/3/14:00:00/UPI/z@ybl/Sent",
            "3 03-01-2026 03-01-2026 200.00 - 6,800.00",
        ])
        r = s.post(f"{API}/statements/preview",
                   files={"file": ("bank.pdf", pdf, "application/pdf")},
                   data={"source": "bank"}, timeout=60)
        su = r.json()["extraction_summary"]
        recon = su["verification"]["reconciliation"]
        # Row 2 broke (9000 - 500 ≠ 7000), row 3 is a valid 7000 - 200 = 6800
        assert recon["mismatches"] >= 1, recon
        assert recon["ok"] is False
        assert su["verification_pass"] is False
        joined = " ".join(su["warnings"])
        assert "balance" in joined.lower()


class TestUnifiedSourceBoth:
    def test_master_tags_merged_pair_as_both(self):
        s = _register("both")
        # Bank statement with a UPI-narrated row that overlaps with a UPI txn.
        bank_csv = (b"Date,Description,Amount,Type\n"
                    b"01/09/2025,UPI/ZOMATO/REF123,450,DR\n"
                    b"05/09/2025,SALARY CREDIT ACME,80000,CR\n")
        prev = s.post(f"{API}/statements/preview",
                      files={"file": ("bank.csv", bank_csv, "text/csv")},
                      data={"source": "bank"}, timeout=30).json()
        pa = s.post(f"{API}/statements/parse",
                    files={"file": ("bank.csv", bank_csv, "text/csv")},
                    data={"mapping": __import__("json").dumps(prev["guess"]), "source": "bank"},
                    timeout=30).json()
        s.post(f"{API}/statements/confirm-import",
               json={"transactions": pa["transactions"], "source": "bank"}, timeout=30)
        # Same Zomato ₹450 arrives via UPI statement — must merge, not duplicate
        upi_csv = (b"Date,Description,Amount,UPI Ref,Merchant\n"
                   b"01/09/2025,Zomato,450,REF123,Zomato\n"
                   b"03/09/2025,Chai Point,80,REF888,Chai Point\n")
        prev = s.post(f"{API}/statements/preview",
                      files={"file": ("upi.csv", upi_csv, "text/csv")},
                      data={"source": "upi"}, timeout=30).json()
        pa = s.post(f"{API}/statements/parse",
                    files={"file": ("upi.csv", upi_csv, "text/csv")},
                    data={"mapping": __import__("json").dumps(prev["guess"]), "source": "upi"},
                    timeout=30).json()
        s.post(f"{API}/statements/confirm-import",
               json={"transactions": pa["transactions"], "source": "upi"}, timeout=30)

        m = s.get(f"{API}/statements/master", timeout=30).json()
        # 2 bank + 2 upi = 4 raw; one bank-upi pair (Zomato) merges → 3 master rows
        assert m["master_count"] == 3, m
        # by_source must contain a 'both' bucket for the merged Zomato pair
        assert "both" in m["by_source"], m
        assert m["by_source"]["both"]["count"] == 1
        assert m["by_source"]["both"]["expense"] == 450.0
