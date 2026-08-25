"""Iteration 20 — multi-page PDF extraction + master dataset endpoint.

Covers the user's complaint that a 6-month PDF was importing only 2 transactions
and the requirement that the master dataset be the single source of truth for
the dashboard (Bank + UPI cross-verified, never double-counted)."""
import io
import os
import uuid
import json
import pytest
import requests
from datetime import date, timedelta

from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.pagesizes import A4

BASE = os.environ.get("FINAURA_API_BASE", "http://localhost:8001")
API = f"{BASE}/api"


def _make_multi_page_pdf() -> bytes:
    rows = [["Date", "Narration", "Chq/Ref", "Value Dt", "Withdrawal Amt.", "Deposit Amt.", "Closing Balance"]]
    merchants = [
        ("UPI-SWIGGY-{r}", 450), ("UPI-ZOMATO ORDER {r}", 380),
        ("UPI-BIGBASKET GROCERY {r}", 2200), ("NEFT UBER TRIP BLR {r}", 350),
        ("NETFLIX SUBSCRIPTION {r}", 649), ("AMAZON INDIA ORDER {r}", 1890),
        ("METRO CARD RECHARGE {r}", 500), ("DR ELECTRICITY BILL {r}", 2540),
        ("UPI-FLIPKART ORDER {r}", 3200), ("RENT PAYMENT NEFT {r}", 32000),
    ]
    balance = 45000
    for m in range(6):
        for i in range(9):
            merch = merchants[(m * 3 + i) % len(merchants)]
            ref = f"REF{m}{i:02d}"
            d = date(2025, 9, 1) + timedelta(days=m * 30 + i * 3)
            balance -= merch[1]
            rows.append([d.strftime("%d/%m/%Y"), merch[0].format(r=ref), ref,
                         d.strftime("%d/%m/%Y"), f"{merch[1]:,.2f}", "",
                         f"{balance:,.2f}"])
        ref = f"SAL{m:02d}"
        d = date(2025, 9, 1) + timedelta(days=m * 30 + 27)
        balance += 80000
        rows.append([d.strftime("%d/%m/%Y"), f"SALARY CREDIT ACME {ref}", ref,
                     d.strftime("%d/%m/%Y"), "", "80,000.00", f"{balance:,.2f}"])

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4)
    styles = getSampleStyleSheet()
    elems = [Paragraph("<b>HDFC BANK — Statement of Account</b>", styles['Title']),
             Paragraph("Statement Period: 01 Sep 2025 to 28 Feb 2026", styles['Normal']),
             Paragraph("Card Number: XXXX-XXXX-XXXX-2026 · Credit Limit ₹2,00,000", styles['Normal']),
             Paragraph("Opening Balance: ₹45,000.00", styles['Normal']),
             Spacer(1, 8)]
    per_page = 25
    header = rows[0]
    body = rows[1:]
    for start in range(0, len(body), per_page):
        chunk = [header] + body[start:start + per_page]
        t = Table(chunk, colWidths=[55, 130, 60, 55, 65, 55, 65])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#003366")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, -1), 7),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ]))
        elems.append(t)
        if start + per_page < len(body):
            elems.append(PageBreak())
    elems.append(Spacer(1, 6))
    elems.append(Paragraph("Page 3 of 3 · Generated on 05 Mar 2026 · IFSC HDFC0000123",
                           styles['Normal']))
    doc.build(elems)
    return buf.getvalue()


def _register():
    email = f"it20_{uuid.uuid4().hex[:8]}@qa.finaura.dev"
    s = requests.Session()
    r = s.post(f"{API}/auth/register",
               json={"email": email, "password": "testpass123", "name": "IT20"},
               timeout=30)
    assert r.status_code == 200, r.text[:300]
    s.headers.update({"Authorization": f"Bearer {r.json()['access_token']}"})
    s.post(f"{API}/auth/onboard", json={"start_with": "blank"}, timeout=30)
    return s


class TestMultiPagePdf:
    def test_six_month_pdf_extracts_all_rows(self):
        s = _register()
        pdf = _make_multi_page_pdf()
        r = s.post(f"{API}/statements/preview",
                   files={"file": ("bank_6mo.pdf", pdf, "application/pdf")},
                   data={"source": "bank"}, timeout=90)
        assert r.status_code == 200, r.text[:300]
        d = r.json()
        assert d["kind"] == "pdf"
        summary = d.get("extraction_summary") or {}
        # 6 months × 10 rows each = 60 transactions
        assert summary.get("transactions_detected") == 60, summary
        assert summary.get("pages_processed") >= 3, summary
        assert summary.get("tables_seen") >= 3, summary
        assert summary.get("credits_count") == 6
        assert summary.get("debits_count") == 54
        assert not summary.get("warnings"), summary["warnings"]
        # No credit-limit noise or "to"-only descriptions
        for t in d["transactions"]:
            desc = (t.get("description") or "").lower()
            assert "credit limit" not in desc
            assert "statement period" not in desc
            assert desc.strip() != "to"


class TestMasterEndpoint:
    def test_master_dedupes_bank_and_upi(self):
        s = _register()
        # Bank CSV
        bank = (b"Date,Description,Amount,Type\n"
                b"01/09/2025,UPI/SWIGGY/REF001,450,DR\n"
                b"02/09/2025,UPI/BIGBASKET/REF002,2200,DR\n"
                b"05/09/2025,SALARY CREDIT ACME,80000,CR\n"
                b"10/09/2025,UPI/AMAZON/REF003,1890,DR\n"
                b"15/09/2025,RENT PAYMENT NEFT,32000,DR\n")
        prev = s.post(f"{API}/statements/preview",
                      files={"file": ("bank.csv", bank, "text/csv")},
                      data={"source": "bank"}, timeout=30).json()
        pa = s.post(f"{API}/statements/parse",
                    files={"file": ("bank.csv", bank, "text/csv")},
                    data={"mapping": json.dumps(prev["guess"]), "source": "bank"},
                    timeout=30).json()
        assert pa["extraction_summary"]["transactions_detected"] == 5
        s.post(f"{API}/statements/confirm-import",
               json={"transactions": pa["transactions"], "source": "bank"}, timeout=30)

        # UPI CSV — 3 rows match bank, 2 are UPI-only
        upi = (b"Date,Description,Amount,UPI Ref,Merchant\n"
               b"01/09/2025,Swiggy,450,REF001,Swiggy\n"
               b"02/09/2025,BigBasket,2200,REF002,BigBasket\n"
               b"10/09/2025,Amazon,1890,REF003,Amazon\n"
               b"12/09/2025,Zomato,320,REF999,Zomato\n"
               b"14/09/2025,Chai Point,80,REF998,Chai Point\n")
        prev = s.post(f"{API}/statements/preview",
                      files={"file": ("upi.csv", upi, "text/csv")},
                      data={"source": "upi"}, timeout=30).json()
        pa = s.post(f"{API}/statements/parse",
                    files={"file": ("upi.csv", upi, "text/csv")},
                    data={"mapping": json.dumps(prev["guess"]), "source": "upi"},
                    timeout=30).json()
        ci = s.post(f"{API}/statements/confirm-import",
                    json={"transactions": pa["transactions"], "source": "upi"},
                    timeout=30).json()
        # 3 rows should have been detected + merged at import time
        assert ci["merged"] == 3, ci

        m = s.get(f"{API}/statements/master", timeout=30).json()
        assert m["master_count"] == 7, m  # 5 bank + 2 upi-only
        assert m["cross_check"]["verified"] == 3
        assert m["cross_check"]["bank_only"] == 2  # rent + salary
        assert m["cross_check"]["upi_only"] == 2  # zomato + chai
        # Expense total must NOT double-count Swiggy/BigBasket/Amazon
        # Bank expenses that survived dedupe: rent 32000 = 32000
        # UPI expenses (unique): zomato 320 + chai 80 = 400
        # Total: 32000 + 400 = 32400 — plus the 3 verified (kept on UPI side): 450 + 2200 + 1890 = 4540
        # So total expenses = 32000 + 400 + 4540 = 36940
        assert abs(m["expense_total"] - 36940.0) < 0.01, m
        assert m["income_total"] == 80000.0
