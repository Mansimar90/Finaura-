"""Iteration 23 — PhonePe + Google Pay UPI PDF formats.

Adds regression coverage for the two major UPI-app PDF layouts beyond Paytm:
* PhonePe — explicit "Paid to X Debit INR NN" / "Received from Y Credit INR NN"
  header with Transaction ID / UTR / Debited-from / Credited-to on wrapped lines.
* Google Pay — spaceless "DDMon,YYYY  PaidtoX ₹NN" header where direction is
  encoded in the phrase ("Paidto..." = debit, "Receivedfrom..." = credit) and
  a "PaidbyBank" / "PaidtoBank" continuation line reconfirms direction."""
import io
import os
import uuid
import requests
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet

BASE = os.environ.get("FINAURA_API_BASE", "http://localhost:8001")
API = f"{BASE}/api"


def _build_phonepe_pdf() -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4)
    styles = getSampleStyleSheet()
    elems = [
        Paragraph("Transaction Statement for +918872100099", styles["Normal"]),
        Paragraph("Feb 26, 2026 - Aug 25, 2026", styles["Normal"]),
        Paragraph("Date Transaction Details Type Amount", styles["Normal"]),
        Spacer(1, 6),
        Paragraph("Feb 28, 2026 Paid to ZOMATO Debit INR 295.83", styles["Normal"]),
        Paragraph("09:03 AM Transaction ID : T2602280903430427006239", styles["Normal"]),
        Paragraph("UTR No : 281638621357", styles["Normal"]),
        Paragraph("Debited from XXXX55", styles["Normal"]),
        Paragraph("Mar 03, 2026 Received from ******9955 Credit INR 2000.00", styles["Normal"]),
        Paragraph("12:51 PM Transaction ID : T2603031251409979176264", styles["Normal"]),
        Paragraph("UTR No : 912157923495", styles["Normal"]),
        Paragraph("Credited to XXXX55", styles["Normal"]),
        Paragraph("Mar 07, 2026 Paid to Spotify India LLP Debit INR 119.00", styles["Normal"]),
        Paragraph("11:39 AM Transaction ID : OLEX2603071139122587553787", styles["Normal"]),
        Paragraph("UTR No : 109330160834", styles["Normal"]),
        Paragraph("Debited from XXXX99", styles["Normal"]),
        Paragraph("Page 1 of 1", styles["Normal"]),
    ]
    doc.build(elems)
    return buf.getvalue()


def _build_gpay_pdf() -> bytes:
    from reportlab.pdfgen import canvas
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    # ReportLab's default Helvetica does not carry the ₹ (U+20B9) glyph, so we
    # register FreeSans (shipped by fonts-freefont-ttf on Debian) which does.
    if "FreeSans" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("FreeSans", "/usr/share/fonts/truetype/freefont/FreeSans.ttf"))
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setFont("FreeSans", 10)
    y = 800
    lines = [
        "Transaction statement",
        "7006127438, gurneetkohli908@gmail.com",
        "Transactionstatementperiod Sent Received",
        f"01February2026-31July2026 {chr(0x20B9)}1,754 {chr(0x20B9)}3,130",
        "Date&time Transactiondetails Amount",
        "",
        f"02Feb,2026 ReceivedfromROHIT {chr(0x20B9)}130",
        "11:56PM UPITransactionID:603327847641",
        "PaidtoJammu&KashmirBank0546",
        f"03Feb,2026 PaidtoZOMATOLIMITED {chr(0x20B9)}537",
        "09:00PM UPITransactionID:640089613438",
        "PaidbyJammu&KashmirBank0546",
        f"05Feb,2026 PaidtoDMRCLimited {chr(0x20B9)}32",
        "11:14AM UPITransactionID:640223799618",
        "PaidbyJammu&KashmirBank0546",
        f"05Feb,2026 ReceivedfromUMESHKUMAR {chr(0x20B9)}3,000",
        "03:44PM UPITransactionID:698734908199",
        "PaidtoJammu&KashmirBank0546",
        f"06Feb,2026 PaidtoAirtel {chr(0x20B9)}1,185",
        "12:19PM UPITransactionID:603753187540",
        "PaidbyJammu&KashmirBank0546",
        "Page 1 of 1",
    ]
    for ln in lines:
        c.drawString(50, y, ln)
        y -= 14
    c.save()
    return buf.getvalue()


def _register(label: str):
    email = f"it23_{label}_{uuid.uuid4().hex[:8]}@qa.finaura.dev"
    s = requests.Session()
    r = s.post(f"{API}/auth/register",
               json={"email": email, "password": "testpass123", "name": label},
               timeout=30)
    assert r.status_code == 200, r.text[:300]
    s.headers.update({"Authorization": f"Bearer {r.json()['access_token']}"})
    s.post(f"{API}/auth/onboard", json={"start_with": "blank"}, timeout=30)
    return s


class TestPhonePeFormat:
    def test_phonepe_debit_credit_words_direction(self):
        s = _register("phonepe")
        r = s.post(f"{API}/statements/preview",
                   files={"file": ("phonepe.pdf", _build_phonepe_pdf(), "application/pdf")},
                   data={"source": "upi"}, timeout=60)
        assert r.status_code == 200
        d = r.json()
        summary = d["extraction_summary"]
        assert summary["transactions_detected"] == 3, d
        assert summary["credits_count"] == 1
        assert summary["debits_count"] == 2
        assert summary["credits_total"] == 2000.0
        assert summary["debits_total"] == 414.83  # 295.83 + 119
        by_amt = {t["amount"]: t for t in d["transactions"]}
        assert by_amt[295.83]["type"] == "Expense"
        assert by_amt[295.83]["merchant"] == "ZOMATO"
        assert by_amt[2000.0]["type"] == "Income"
        assert by_amt[119.0]["merchant"] == "Spotify India LLP"
        # UPI ref pulled from "UTR No" line
        assert by_amt[295.83]["upi_ref"] == "281638621357"
        # App tag
        assert by_amt[295.83].get("upi_app") == "phonepe"


class TestGPayFormat:
    def test_gpay_paidto_vs_receivedfrom_direction(self):
        s = _register("gpay")
        r = s.post(f"{API}/statements/preview",
                   files={"file": ("gpay.pdf", _build_gpay_pdf(), "application/pdf")},
                   data={"source": "upi"}, timeout=60)
        assert r.status_code == 200
        d = r.json()
        summary = d["extraction_summary"]
        assert summary["transactions_detected"] == 5, d
        assert summary["credits_count"] == 2  # ROHIT + UMESHKUMAR
        assert summary["debits_count"] == 3  # ZOMATO + DMRC + Airtel
        by_amt = {t["amount"]: t for t in d["transactions"]}
        assert by_amt[130.0]["type"] == "Income"
        assert "ROHIT" in by_amt[130.0]["merchant"]
        assert by_amt[537.0]["type"] == "Expense"
        assert "ZOMATO" in by_amt[537.0]["merchant"]
        assert by_amt[32.0]["type"] == "Expense"
        assert by_amt[3000.0]["type"] == "Income"
        assert by_amt[1185.0]["type"] == "Expense"
        # Statement period header ("01February2026-31July2026 ₹1,754 ₹3,130")
        # must NOT surface as a transaction
        amounts = {t["amount"] for t in d["transactions"]}
        assert 1754 not in amounts
        assert 3130 not in amounts
        # App tag
        assert by_amt[130.0].get("upi_app") == "gpay"
