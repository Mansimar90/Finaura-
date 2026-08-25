"""Statement upload & parser — CSV, Excel, PDF text extraction."""
from __future__ import annotations

import io
import os
import json as _json_mod
import re
import uuid
import logging
from datetime import datetime, timezone
from typing import Any

import pandas as pd
from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Depends
from pydantic import BaseModel
from motor.motor_asyncio import AsyncIOMotorDatabase

log = logging.getLogger("finaura.statements")

CATEGORY_KEYWORDS = {
    "Food": ["swiggy", "zomato", "restaurant", "cafe", "coffee", "starbucks", "domino", "pizza", "food", "eat", "dine", "biryani", "chai", "dhaba", "burger"],
    "Rent": ["rent", "landlord", "housing"],
    "Bills": ["electricity", "water", "gas", "internet", "wifi", "airtel", "jio", "vodafone", "bill", "utility", "recharge", "postpaid", "prepaid", "bescom", "adani"],
    "Transport": ["uber", "ola", "metro", "petrol", "fuel", "taxi", "cab", "rapido", "irctc", "indianoil", "hpcl", "bpcl", "auto"],
    "Shopping": ["amazon", "flipkart", "myntra", "ajio", "shopping", "mall", "nykaa", "meesho", "bigbasket", "blinkit", "zepto", "dmart", "grocery"],
    "Entertainment": ["netflix", "spotify", "prime", "hotstar", "cinema", "bookmyshow", "youtube", "sony", "disney", "jiocinema"],
    "Healthcare": ["hospital", "pharma", "medi", "chemist", "clinic", "apollo", "1mg", "netmeds", "practo", "cure"],
    "Education": ["school", "college", "coursera", "udemy", "tuition", "fee", "byju", "unacademy", "vedantu"],
    "Income": ["salary", "payroll", "credit", "interest", "dividend", "refund", "cashback"],
}


# ---------- UPI helpers ----------

UPI_APP_HINTS = {
    "google pay": "gpay",
    "gpay": "gpay",
    "google.com/pay": "gpay",
    "phonepe": "phonepe",
    "phonepe.com": "phonepe",
    "paytm": "paytm",
    "bhim": "bhim",
    "amazonpay": "amazonpay",
    "cred": "cred",
}


def detect_upi_app(text: str) -> str | None:
    if not text:
        return None
    t = text.lower()
    for hint, key in UPI_APP_HINTS.items():
        if hint in t:
            return key
    return None


UPI_HANDLE_RE = re.compile(r"([a-zA-Z0-9.\-_]{2,})@([a-zA-Z][a-zA-Z0-9]{1,})", re.IGNORECASE)
UPI_TXN_ID_RE = re.compile(r"\b((?:UPI|UTR|Txn|TXN|Ref|Reference|Transaction ID)[^\w]{0,3})?(\d{9,22})\b")


def extract_upi_meta(description: str) -> dict:
    """Extract UPI-specific fields from a free-text description/narration."""
    if not description:
        return {}
    text = str(description)
    meta: dict = {}
    handles = UPI_HANDLE_RE.findall(text)
    if handles:
        # Prefer the last handle (usually the merchant, first is often the payer)
        h = handles[-1]
        meta["upi_id"] = f"{h[0]}@{h[1]}"
    # Try to find a transaction reference — long digits string
    m = UPI_TXN_ID_RE.search(text)
    if m:
        meta["upi_ref"] = m.group(2)
    # Merchant = first meaningful token before the UPI id
    if handles:
        pre_handle = text.split(meta.get("upi_id", ""))[0]
        # strip common UPI verbs
        cleaned = re.sub(r"\b(paid to|received from|payment to|txn to|from|to)\b", "", pre_handle, flags=re.IGNORECASE).strip(" -/|,")
        if cleaned:
            meta["merchant"] = cleaned[:80]
    return meta


def guess_category(description: str, txn_type: str) -> str:
    d = (description or "").lower()
    # Detect internal transfers first — a "credit" from self is NOT income
    if any(hint in d for hint in INTERNAL_TRANSFER_HINTS):
        return "Internal Transfer"
    if txn_type == "Income":
        for cat, kws in CATEGORY_KEYWORDS.items():
            if any(k in d for k in kws):
                return cat if cat == "Income" else "Miscellaneous Credit"
        # No confident classification — surface as Miscellaneous Credit, NOT auto-Income
        return "Miscellaneous Credit"
    # Expense branch
    for cat, kws in CATEGORY_KEYWORDS.items():
        if cat == "Income":
            continue
        if any(k in d for k in kws):
            return cat
    return "Miscellaneous Debit"


def resolve_type_and_category(desc: str, txn_type: str, category: str | None) -> tuple[str, str]:
    """Central rule: an internal-transfer credit must never become 'Income'."""
    d = (desc or "").lower()
    if any(hint in d for hint in INTERNAL_TRANSFER_HINTS):
        # Internal transfer — keep type direction but flag category
        return txn_type, "Internal Transfer"
    if category and category in ALLOWED_CATEGORIES:
        return txn_type, category
    return txn_type, guess_category(desc, txn_type)


def normalize_amount(value: Any) -> float:
    if value is None:
        return 0.0
    try:
        if pd.isna(value):
            return 0.0
    except (TypeError, ValueError):
        pass
    if isinstance(value, (int, float)):
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0
    s = str(value).strip()
    if not s or s.lower() == "nan":
        return 0.0
    s = s.replace("₹", "").replace("Rs.", "").replace("Rs", "").replace("INR", "").strip()
    s = s.replace(",", "").replace(" ", "")
    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative = True
        s = s[1:-1]
    if s.startswith("-"):
        negative = True
        s = s[1:]
    try:
        v = float(s)
        if v != v:  # NaN check
            return 0.0
        return -v if negative else v
    except ValueError:
        return 0.0


def normalize_date(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return datetime.now(timezone.utc).strftime("%d %b %Y")
    if isinstance(value, (datetime, pd.Timestamp)):
        try:
            return pd.Timestamp(value).strftime("%d %b %Y")
        except Exception:
            pass
    s = str(value).strip()
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%m/%d/%Y", "%d %b %Y", "%d %B %Y", "%d-%b-%Y", "%d %b %y"):
        try:
            return datetime.strptime(s, fmt).strftime("%d %b %Y")
        except ValueError:
            continue
    try:
        return pd.to_datetime(s, dayfirst=True).strftime("%d %b %Y")
    except Exception:
        return s or datetime.now(timezone.utc).strftime("%d %b %Y")


def _detect_type(amount: float, hint: str | None = None) -> tuple[str, float]:
    """Return (type, absolute amount). Prefer explicit hint like 'CR'/'DR'/'Sent'/'Received'."""
    if hint:
        h = hint.strip().lower()
        if h in ("credit", "cr", "c", "in", "income", "+", "received", "receive", "money in", "money received"):
            return "Income", abs(amount)
        if h in ("debit", "dr", "d", "out", "expense", "-", "sent", "send", "paid", "money out", "money sent"):
            return "Expense", abs(amount)
    if amount > 0:
        return "Income", amount
    return "Expense", abs(amount)


# -------- CSV parsing --------

def csv_preview(content: bytes, source: str = "bank") -> dict:
    """Return columns + first 5 rows so the client can map fields."""
    text = content.decode("utf-8", errors="ignore")
    df = pd.read_csv(io.StringIO(text), nrows=50, on_bad_lines="skip")
    df.columns = [str(c).strip() for c in df.columns]
    columns = list(df.columns)
    guess = _auto_map_columns(columns, source=source)
    rows = df.head(5).fillna("").astype(str).to_dict(orient="records")
    return {"columns": columns, "rows": rows, "guess": guess, "total_rows": len(df)}


def _auto_map_columns(columns: list[str], source: str = "bank") -> dict:
    lower = {c.lower(): c for c in columns}
    # Short aliases (2-3 chars like 'cr', 'dr') must match as whole tokens so they
    # don't hit substrings inside longer column names (e.g. 'cr' inside 'Description').
    SHORT = {"cr", "dr", "c", "d"}
    # Columns that look like a date must never be picked for the amount slot even
    # if they contain a substring like 'value' (e.g. 'Value Date', 'Value Dt').
    def is_date_col(k: str) -> bool:
        toks = re.split(r"[^a-z0-9]+", k)
        return any(t in ("date", "dt") for t in toks)
    def find(*names, skip_date_cols: bool = False):
        for n in names:
            for k, v in lower.items():
                if skip_date_cols and is_date_col(k):
                    continue
                if n in SHORT:
                    tokens = re.split(r"[^a-z0-9]+", k)
                    if n in tokens:
                        return v
                elif n in k:
                    return v
        return None
    base = {
        "date": find("date", "txn date", "value date", "posting", "transaction time", "time"),
        "description": find("description", "narration", "particulars", "details", "remarks", "note", "to / from", "merchant"),
        # Amount must not accidentally map to a date column named "Value Date".
        "amount": find("amount", "txn amount", "value", skip_date_cols=True),
        "debit": find("debit", "withdrawal", "withdrawl", "dr"),
        "credit": find("credit", "deposit", "cr"),
        "type": find("type", "cr/dr", "dr/cr", "direction", "payment type"),
    }
    if source == "upi":
        base["upi_ref"] = find("upi ref", "upi reference", "ref no", "utr", "reference id", "reference no")
        base["txn_id"] = find("transaction id", "txn id", "transaction no")
        base["upi_id"] = find("upi id", "vpa", "payee vpa", "payer vpa")
        base["merchant"] = find("merchant", "to", "recipient", "payee", "beneficiary")
    return base


def parse_csv(content: bytes, mapping: dict, source: str = "bank") -> list[dict]:
    text = content.decode("utf-8", errors="ignore")
    df = pd.read_csv(io.StringIO(text), on_bad_lines="skip")
    df.columns = [str(c).strip() for c in df.columns]
    transactions: list[dict] = []
    for _, row in df.iterrows():
        date_val = row.get(mapping.get("date")) if mapping.get("date") else None
        desc_val = row.get(mapping.get("description")) if mapping.get("description") else ""
        type_hint = row.get(mapping.get("type")) if mapping.get("type") else None
        amount = 0.0
        txn_type = "Expense"
        if mapping.get("amount"):
            amount = normalize_amount(row.get(mapping["amount"]))
            txn_type, amount = _detect_type(amount, str(type_hint) if type_hint else None)
        # If amount didn't yield a real number (e.g. an amount column was mis-detected
        # as a date column), fall back to Debit/Credit pair when either is available.
        if amount <= 0 and (mapping.get("debit") or mapping.get("credit")):
            dr = normalize_amount(row.get(mapping.get("debit"))) if mapping.get("debit") else 0
            cr = normalize_amount(row.get(mapping.get("credit"))) if mapping.get("credit") else 0
            if cr:
                txn_type = "Income"; amount = abs(cr)
            elif dr:
                txn_type = "Expense"; amount = abs(dr)
        if amount > 0 and mapping.get("amount"):
            # Bank + UPI heuristic: when the statement has only an unsigned Amount column
            # (no Debit/Credit split, no Type/CR/DR hint), positive amounts are outbound
            # payments UNLESS the narration or merchant looks like an inflow. This prevents
            # everyday debits (Swiggy, BigBasket, etc.) from being misread as Income.
            if not type_hint and not (mapping.get("debit") or mapping.get("credit")):
                merchant_val = str(row.get(mapping.get("merchant"), "") or "").lower() if mapping.get("merchant") else ""
                desc_check = f"{str(desc_val) or ''} {merchant_val}".lower()
                income_kws = CATEGORY_KEYWORDS["Income"] + [
                    "received from", "credited by", "money in", "salary", "payroll",
                    "refund", "cashback", "interest credit", "dividend",
                ]
                is_receive = any(kw in desc_check for kw in income_kws)
                txn_type = "Income" if is_receive else "Expense"
        if amount <= 0 or not str(desc_val).strip():
            continue
        desc = str(desc_val).strip()[:120]
        cat_source = desc
        if source == "upi" and mapping.get("merchant"):
            m_val = str(row.get(mapping.get("merchant"), "") or "").strip()
            if m_val:
                cat_source = f"{m_val} {desc}"
        # Central classification — internal transfers never leak into 'Income'
        txn_type, category = resolve_type_and_category(cat_source, txn_type, None)
        txn = {
            "id": str(uuid.uuid4()),
            "date": normalize_date(date_val),
            "description": desc,
            "amount": round(amount, 2),
            "type": txn_type,
            "category": category,
            "source": source,
        }
        if source == "upi":
            upi_meta = extract_upi_meta(desc)
            for k in ("upi_ref", "txn_id", "upi_id", "merchant"):
                col = mapping.get(k)
                if col and str(row.get(col, "")).strip():
                    upi_meta[k] = str(row.get(col)).strip()[:80]
            txn.update({k: v for k, v in upi_meta.items() if v})
            app_key = detect_upi_app(desc) or (upi_meta.get("upi_id", "").split("@")[-1] if upi_meta.get("upi_id") else None)
            if app_key:
                txn["upi_app"] = app_key
        transactions.append(txn)
    return transactions


# -------- Excel parsing --------

def parse_excel(content: bytes, mapping: dict, source: str = "bank") -> list[dict]:
    df = pd.read_excel(io.BytesIO(content))
    df.columns = [str(c).strip() for c in df.columns]
    csv_bytes = df.to_csv(index=False).encode("utf-8")
    return parse_csv(csv_bytes, mapping, source=source)


def excel_preview(content: bytes, source: str = "bank") -> dict:
    df = pd.read_excel(io.BytesIO(content), nrows=50)
    df.columns = [str(c).strip() for c in df.columns]
    columns = list(df.columns)
    return {
        "columns": columns,
        "rows": df.head(5).fillna("").astype(str).to_dict(orient="records"),
        "guess": _auto_map_columns(columns, source=source),
        "total_rows": len(df),
    }


# -------- PDF parsing (table-first, text-fallback) --------

DATE_PATTERNS = [
    r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\b",
    r"\b(\d{1,2}\s+[A-Za-z]{3,9}\s+\d{2,4})\b",
    r"\b(\d{4}-\d{2}-\d{2})\b",
]
AMOUNT_PATTERN = r"([₹]?\s?[+-]?\s?\d{1,3}(?:,\d{2,3})+(?:\.\d{1,2})?|[₹]?\s?[+-]?\s?\d+(?:\.\d{1,2})?)"


DEBIT_RE = re.compile(r"\b(dr|debit|withdrawal|withdraw)\b", re.IGNORECASE)
CREDIT_RE = re.compile(r"\b(cr|credit|deposit)\b", re.IGNORECASE)

# Lines / rows containing any of these are almost always account-summary noise
# (credit limit, opening balance, statement period, card number, page-N-of-M, etc.)
# — NOT actual transactions.
NON_TXN_HINTS = (
    "credit limit", "available limit", "cash limit", "total due", "minimum due",
    "opening balance", "closing balance", "available balance", "book balance",
    "statement period", "statement date", "statement summary", "account summary",
    "card number", "customer id", "ifsc", "micr", "branch code",
    "page ", "generated on", "printed on", "as on", "valid till", "due date",
    "cheque book", "statement of account", "reward points", "gst", "vat",
    "interest paid", "opening", "carried forward", "brought forward",
    "grand total", "sub total", "total credits", "total debits",
)


def _row_looks_like_txn(row_txt: str) -> bool:
    low = (row_txt or "").lower()
    if len(row_txt) < 8:
        return False
    if any(h in low for h in NON_TXN_HINTS):
        return False
    # Must contain at least one digit sequence >= 2 digits (an amount)
    if not re.search(r"\d{2,}", row_txt):
        return False
    return True


def _pick_amount(cells: list[str]) -> tuple[float, str | None]:
    """Given a table row's cells, find the transaction amount.
    Return (amount, hint) where hint is 'debit', 'credit', or None."""
    numeric = []
    for i, c in enumerate(cells):
        v = normalize_amount(c)
        if v > 0:
            numeric.append((i, v, c))
    if not numeric:
        return 0.0, None
    # Heuristic: bank statements typically place Debit / Credit / Balance in the
    # last 2-3 columns. The BALANCE (largest, monotonic) is usually the last one
    # → drop it. What remains is the actual movement.
    # If there are exactly 2+ numeric cells, drop the LAST (running balance).
    if len(numeric) >= 2:
        # last one is balance
        numeric = numeric[:-1]
    # The remaining non-zero cell is the movement.
    i, amt, raw = numeric[-1]
    # Detect debit vs credit by header proximity — caller passes cell index; here we
    # just try to infer from whether the row also carries CR/DR tokens.
    return amt, None


def _extract_tables(pdf) -> list[list[list[str]]]:
    tables: list[list[list[str]]] = []
    for page in pdf.pages:
        try:
            found = page.extract_tables() or []
        except Exception:
            found = []
        for tbl in found:
            if tbl and len(tbl) >= 2:
                tables.append(tbl)
    return tables


def _classify_columns(header: list[str]) -> dict:
    """Map a table header row to column indices we care about. Rejects merged /
    unusable headers where multiple critical column names collapse into a single
    cell (common on BoB / Hindi-bilingual statements)."""
    if not header:
        return {}
    # Reject the merged-header case: cells 1..N are all empty AND cell 0 carries
    # 2+ of our header keywords. This happens when pdfplumber flattens a bank's
    # bilingual / stacked header row into one giant cell — column classification
    # would fabricate a mapping that all points to cell 0 and every row would
    # then be counted as Income. Caller should fall through to text-based parsing.
    if len(header) >= 3 and (header[0] or "").strip():
        first = (header[0] or "").lower()
        rest_empty = all(not (c or "").strip() for c in header[1:])
        hits = sum(1 for kw in ("debit", "credit", "balance", "description",
                                 "narration", "amount", "withdrawal", "deposit",
                                 "cheque", "date", "value", "particulars", "remarks")
                   if kw in first)
        if rest_empty and hits >= 2:
            return {}
    idx: dict[str, int] = {}
    for i, cell in enumerate(header or []):
        h = (cell or "").strip().lower()
        if not h:
            continue
        if "date" in h and "date" not in idx:
            idx["date"] = i
        if any(k in h for k in ("narration", "particulars", "description", "details", "remarks", "transaction")) and "desc" not in idx:
            idx["desc"] = i
        if any(k in h for k in ("withdrawal", "debit", "dr amount", "paid out", "amount debit")) and "debit" not in idx:
            idx["debit"] = i
        if any(k in h for k in ("deposit", "credit", "cr amount", "paid in", "amount credit")) and "credit" not in idx:
            idx["credit"] = i
        if h == "amount" or h.endswith(" amount") or h.startswith("amount"):
            if "amount" not in idx:
                idx["amount"] = i
        if "balance" in h and "balance" not in idx:
            idx["balance"] = i
        if any(k in h for k in ("type", "cr/dr", "dr/cr")) and "type" not in idx:
            idx["type"] = i
    return idx


def parse_pdf(content: bytes, source: str = "bank") -> tuple[list[dict], str, dict]:
    """Extract transactions from a PDF. Returns (transactions, sample_text, meta)
    where meta = {'pages_processed': int, 'tables_seen': int, 'text_fallback_used': bool,
    'warnings': [...]}"""
    try:
        import pdfplumber
    except ImportError as exc:
        raise HTTPException(status_code=503, detail="PDF parser not available on this server.") from exc
    transactions: list[dict] = []
    sample_snippet = ""
    meta = {"pages_processed": 0, "tables_seen": 0, "text_fallback_used": False, "warnings": []}
    try:
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            meta["pages_processed"] = len(pdf.pages)
            # --- Strategy 1: proper table extraction ---
            for page in pdf.pages:
                try:
                    tables = page.extract_tables() or []
                except Exception:
                    tables = []
                for tbl in tables:
                    if not tbl or len(tbl) < 2:
                        continue
                    meta["tables_seen"] += 1
                    # First row is usually the header. Find one that has a Date-ish
                    # column so we're not mining a summary table.
                    header_i = 0
                    col_idx = _classify_columns(tbl[0])
                    if "date" not in col_idx:
                        # Try second row as header (some PDFs stack a title above)
                        col_idx = _classify_columns(tbl[1]) if len(tbl) > 1 else {}
                        if "date" not in col_idx:
                            continue
                        header_i = 1
                    for raw_row in tbl[header_i + 1 :]:
                        row = [(c or "").strip() for c in raw_row]
                        row_text = " | ".join(row)
                        if not _row_looks_like_txn(row_text):
                            continue
                        date_cell = row[col_idx["date"]] if col_idx.get("date") is not None and col_idx["date"] < len(row) else ""
                        # Grab first plausible date from the cell
                        date_match = None
                        for pat in DATE_PATTERNS:
                            m = re.search(pat, date_cell)
                            if m:
                                date_match = m
                                break
                        if not date_match:
                            continue
                        desc_cell = row[col_idx["desc"]] if col_idx.get("desc") is not None and col_idx["desc"] < len(row) else ""
                        desc = re.sub(r"\s+", " ", desc_cell).strip()
                        if not desc:
                            # Fallback: join all non-date/non-amount cells
                            skip_cols = {col_idx.get("date"), col_idx.get("debit"), col_idx.get("credit"),
                                         col_idx.get("amount"), col_idx.get("balance"), col_idx.get("type")}
                            desc = " ".join(c for i, c in enumerate(row) if i not in skip_cols and c).strip()
                        if not desc:
                            continue
                        # Determine amount + direction from the debit/credit split when available,
                        # otherwise fall back to a single Amount column.
                        amount = 0.0
                        hint = None
                        if col_idx.get("debit") is not None or col_idx.get("credit") is not None:
                            dr = normalize_amount(row[col_idx["debit"]]) if col_idx.get("debit") is not None and col_idx["debit"] < len(row) else 0
                            cr = normalize_amount(row[col_idx["credit"]]) if col_idx.get("credit") is not None and col_idx["credit"] < len(row) else 0
                            if cr and (not dr or cr >= dr):
                                amount, hint = cr, "credit"
                            elif dr:
                                amount, hint = dr, "debit"
                        elif col_idx.get("amount") is not None and col_idx["amount"] < len(row):
                            amount = normalize_amount(row[col_idx["amount"]])
                            if col_idx.get("type") is not None and col_idx["type"] < len(row):
                                type_cell = (row[col_idx["type"]] or "").lower()
                                if "cr" in type_cell or "credit" in type_cell or "received" in type_cell:
                                    hint = "credit"
                                elif "dr" in type_cell or "debit" in type_cell or "sent" in type_cell:
                                    hint = "debit"
                        else:
                            amt, _ = _pick_amount(row)
                            amount = amt
                        if amount <= 0:
                            continue
                        # Word-boundary CR/DR scan across the whole row (as extra hint)
                        if not hint:
                            if CREDIT_RE.search(row_text):
                                hint = "credit"
                            elif DEBIT_RE.search(row_text):
                                hint = "debit"
                        txn_type, absolute_amount = _detect_type(amount, hint)
                        if absolute_amount <= 0:
                            continue
                        # Trim any trailing DR/CR markers still in the description
                        desc = CREDIT_RE.sub("", desc)
                        desc = DEBIT_RE.sub("", desc)
                        desc = re.sub(r"\s+", " ", desc).strip(" -\t|")
                        if not desc or len(desc) < 3:
                            continue
                        txn_type, category = resolve_type_and_category(desc, txn_type, None)
                        txn = {
                            "id": str(uuid.uuid4()),
                            "date": normalize_date(date_match.group(1)),
                            "description": desc[:120],
                            "amount": round(absolute_amount, 2),
                            "type": txn_type,
                            "category": category,
                            "source": source,
                        }
                        if source == "upi":
                            upi_meta = extract_upi_meta(desc)
                            txn.update({k: v for k, v in upi_meta.items() if v})
                            app_key = detect_upi_app(desc)
                            if app_key:
                                txn["upi_app"] = app_key
                        transactions.append(txn)
            # Capture a sample for the UI even if tables found rows
            for page in pdf.pages[:1]:
                try:
                    t = page.extract_text() or ""
                    sample_snippet = "\n".join([ln for ln in t.splitlines() if ln.strip()][:6])
                    break
                except Exception:
                    pass

            # --- Strategy 1.5: multi-line RECORD parser (Paytm / GPay / PhonePe app exports) ---
            # Detect statements where each transaction spans multiple lines with a leading
            # short date like "24 Aug 9:39 PM ... - Rs.249" or "23 Aug ... + Rs.500".
            if not transactions:
                # Concatenate the full document text once, group into records.
                doc_lines: list[str] = []
                for page in pdf.pages:
                    text = page.extract_text() or ""
                    for ln in text.splitlines():
                        s = ln.strip()
                        if not s:
                            continue
                        low = s.lower()
                        # Drop obvious page-footer / summary noise
                        if any(h in low for h in ("page ", "for any queries", "contact us",
                                                  "passbook payments history",
                                                  "date &", "date & time", "transaction details",
                                                  "notes & tags", "your account",
                                                  "all payments done by you",
                                                  "self transfer payments",
                                                  "payments that you might have")):
                            continue
                        # Drop lone header fragments (single word column headers)
                        if low in ("time", "amount", "date", "tag:", "note:", "your account"):
                            continue
                        # Drop lines that ARE JUST a bank/account column value bleed
                        if re.match(r"^\s*(bank\s+of|baroda|hdfc|icici|sbi|axis|kotak)\b[\w\s\-]{0,25}$", low):
                            continue
                        doc_lines.append(s)
                # Infer statement year from a "25 FEB'26 - 24 AUG'26" header if present
                stmt_year = None
                for ln in doc_lines[:60]:
                    ym = re.search(r"[A-Za-z]{3,9}\W?'?(\d{2}|\d{4})", ln)
                    if ym:
                        yv = ym.group(1)
                        stmt_year = int(yv) if len(yv) == 4 else 2000 + int(yv)
                        break
                # Regex for a Paytm record header line — starts with "DD Mon"
                # and ends with a signed rupee amount. Use anchored fragments so we
                # can strip the prefix and suffix but keep the middle description.
                REC_DATE_PREFIX = re.compile(r"^(\d{1,2})\s+([A-Za-z]{3,9})\b\s*")
                REC_AMT_SUFFIX = re.compile(r"([+-])\s*(?:Rs\.?|₹|INR)\s*([\d,]+(?:\.\d{1,2})?)\s*$")
                def _match_rec(s: str):
                    dp = REC_DATE_PREFIX.match(s)
                    if not dp:
                        return None
                    ap = REC_AMT_SUFFIX.search(s)
                    if not ap:
                        return None
                    return (dp, ap)
                current: list[str] = []
                records: list[list[str]] = []
                def _flush():
                    if current and _match_rec(current[0] or ""):
                        records.append(list(current))
                for ln in doc_lines:
                    if _match_rec(ln):
                        _flush()
                        current = [ln]
                    elif current:
                        current.append(ln)
                _flush()

                UPI_REF_RE = re.compile(r"UPI\s*Ref\s*No\.?\s*:?\s*(\d{6,22})", re.IGNORECASE)
                UPI_ID_RE = re.compile(r"UPI\s*ID\s*:?\s*([\w.\-]+@[\w\-]+)", re.IGNORECASE)
                BANK_REF_RE = re.compile(r"Bank\s*Ref\s*No\.?\s*:?\s*(\d{6,22})", re.IGNORECASE)
                ORDER_ID_RE = re.compile(r"Order\s*ID\s*:?\s*(\d{6,22})", re.IGNORECASE)
                for rec in records:
                    header = rec[0]
                    parts = _match_rec(header)
                    if not parts:
                        continue
                    dp, ap = parts
                    day, mon = dp.group(1), dp.group(2)
                    sign, amt_str = ap.group(1), ap.group(2)
                    amount = normalize_amount(amt_str)
                    if amount <= 0:
                        continue
                    header_low = header.lower()
                    # Skip statement summary / total-money lines (they carry a Rs. amount
                    # like the real records but describe aggregate totals, not one txn).
                    if any(k in header_low for k in (
                        "payment received", "payment made", "payments received",
                        "payments made", "total money", "money paid", "money received",
                        "grand total", "opening balance", "closing balance",
                    )):
                        continue
                    # Reject headers that carry a date RANGE (e.g. "25 FEB'26 - 24 AUG'26")
                    # — those are the statement period, not a transaction.
                    if len(re.findall(r"[A-Za-z]{3,9}\W?'?\d{2,4}", header)) >= 2:
                        continue
                    # Reject if description contains 2+ Rs. amounts (summary row)
                    if len(re.findall(r"Rs\.?", header)) >= 2:
                        continue
                    # Direction: '+' = money in (Income), '-' = money out (Expense)
                    txn_type = "Income" if sign == "+" else "Expense"
                    # Description: strip the leading date and trailing amount from header,
                    # append merchant / counterparty hints from context lines.
                    header_desc = REC_AMT_SUFFIX.sub("", REC_DATE_PREFIX.sub("", header)).strip()
                    # Trim the phrase like "Automatic payment of ₹1649 setup for" — keep merchant
                    ctx_text = " ".join(rec[1:6])
                    upi_ref_m = UPI_REF_RE.search(ctx_text) or BANK_REF_RE.search(ctx_text)
                    upi_id_m = UPI_ID_RE.search(ctx_text)
                    order_m = ORDER_ID_RE.search(ctx_text)
                    # Merchant heuristic: description often starts with 'Paid to X' /
                    # 'Received from X' / 'Money sent to X' — extract X (stop at UPI/Tag/Note/Bank).
                    merchant = None
                    for phrase in ("Paid to ", "Received from ", "Money sent to ", "Sent to "):
                        if phrase in header_desc:
                            m2 = header_desc.split(phrase, 1)[1]
                            m2 = re.split(r"\s+(?:Note|Tag|UPI|Bank|Order|Ref)\s*:", m2, maxsplit=1)[0]
                            m2 = re.sub(r"\s+Bank\s+Of\b.*$", "", m2, flags=re.IGNORECASE)
                            m2 = m2.strip(" -\t")
                            if m2:
                                merchant = m2
                            break
                    if not merchant:
                        # Fallback: first "content" line after the header often holds the
                        # wrapped merchant name (before mixed Notes / Account column bleed).
                        for cont in rec[1:5]:
                            if any(kw in cont for kw in ("UPI ID:", "UPI Ref", "Tag:", "Note:", "Bank Ref", "Order ID", "Page ")):
                                continue
                            low_c = cont.strip().lower()
                            if low_c in ("time", "amount", "date"):
                                continue
                            if re.match(r"^\d{1,2}[:.]\d{2}\s*(?:AM|PM|am|pm)?\s*$", cont):
                                continue  # time-only line
                            # UPI-ID-only fragment (e.g. "4s8sr@paytm on") — skip
                            if re.match(r"^[\w.\-]+@[\w\-]+(\s+on)?\s*$", cont, re.IGNORECASE):
                                continue
                            # Trim trailing column bleed (Notes/Bank columns)
                            cleaned = re.sub(r"\s+Bank\s+Of\b.*$", "", cont, flags=re.IGNORECASE)
                            cleaned = re.sub(r"\s+Baroda\b.*$", "", cleaned, flags=re.IGNORECASE)
                            cleaned = re.sub(r"\s+Razorpay\b.*$", "", cleaned, flags=re.IGNORECASE)
                            cleaned = re.sub(r"\s+#\s*\w+.*$", "", cleaned)  # trim '# Food' tag column
                            cleaned = re.sub(r"\s+on\s*$", "", cleaned)  # trailing "on"
                            cleaned = cleaned.strip(" -\t")
                            if cleaned and len(cleaned) >= 3 and not cleaned.lower().startswith("upi"):
                                merchant = cleaned
                                break
                    # Prefer merchant when header_desc is generic ("Automatic payment of ...")
                    if merchant and re.search(r"automatic payment|setup for|scheduled|standing instruction", (header_desc or "").lower()):
                        desc_out = merchant
                    else:
                        # Clean up header_desc — drop Notes/Tag/Bank column bleed markers
                        cleaned_hd = re.sub(r"\s*(?:Note|Tag)\s*:.*$", "", header_desc)
                        cleaned_hd = re.sub(r"\s+Bank\s+Of\b.*$", "", cleaned_hd, flags=re.IGNORECASE)
                        cleaned_hd = cleaned_hd.strip(" -\t#")
                        desc_out = cleaned_hd or merchant or "UPI payment"
                    # Normalise date
                    year = stmt_year or datetime.now(timezone.utc).year
                    try:
                        date_norm = datetime.strptime(f"{day} {mon} {year}", "%d %b %Y").strftime("%d %b %Y")
                    except ValueError:
                        try:
                            date_norm = datetime.strptime(f"{day} {mon} {year}", "%d %B %Y").strftime("%d %b %Y")
                        except ValueError:
                            continue
                    desc = desc_out[:120] if desc_out else (merchant or "UPI payment")
                    txn_type, category = resolve_type_and_category(f"{merchant or ''} {desc}", txn_type, None)
                    txn = {
                        "id": str(uuid.uuid4()),
                        "date": date_norm,
                        "description": desc,
                        "amount": round(amount, 2),
                        "type": txn_type,
                        "category": category,
                        "source": source,
                    }
                    if merchant:
                        txn["merchant"] = merchant[:80]
                    if upi_ref_m:
                        txn["upi_ref"] = upi_ref_m.group(1)
                    if upi_id_m:
                        txn["upi_id"] = upi_id_m.group(1)
                        app_key = upi_id_m.group(1).split("@")[-1].lower()
                        if app_key:
                            txn["upi_app"] = app_key
                    if order_m:
                        txn["txn_id"] = order_m.group(1)
                    transactions.append(txn)
                if transactions:
                    meta["text_fallback_used"] = True
                    meta["record_parser_used"] = True

            # --- Strategy 1.7: numbered-row BANK statement (BoB, SBI, HDFC, ICICI, PNB) ---
            # Pattern: '<sr> <date> [<value_date>] <optional_debit> <optional_credit> <balance>'
            # where dates may use '-', '.', or '/' and description text is on lines ABOVE
            # and/or BELOW the row. Debit-vs-Credit is decided by the running-balance
            # delta so we work with any bank layout (with or without an explicit '-' column).
            if not transactions:
                all_lines: list[str] = []
                for page in pdf.pages:
                    text = page.extract_text() or ""
                    for ln in text.splitlines():
                        s = ln.strip()
                        if s:
                            all_lines.append(s)
                # A "row line" is: srno date [second date] one-or-more amounts, ending in a
                # balance-shaped decimal. Amounts may be a placeholder '-' or a number.
                ROW_RE = re.compile(
                    r"^(\d{1,4})\s+"                                    # sr no
                    r"(\d{1,2}[-./]\d{1,2}[-./]\d{2,4})"                # transaction date
                    r"(?:\s+\d{1,2}[-./]\d{1,2}[-./]\d{2,4})?"          # optional value date
                    r"\s+(.+?)\s+"                                       # middle numbers/placeholders
                    r"([\d,]+\.\d{2})\s*$"                              # closing balance (must have decimals)
                )
                NUM_TOKEN = re.compile(r"^(?:-|[\d,]+(?:\.\d{1,2})?)$")

                # Filter out header noise so it isn't confused for a description line.
                noise_prefixes = ("account statement", "statement of transaction", "opening balance",
                                  "closing balance", "s no", "sr.no", "transaction date",
                                  "withdrawal", "deposit", "amount (inr)", "balance (inr)",
                                  "s no.", "cheque number", "for any queries", "generated on")
                def _is_desc_line(ln: str) -> bool:
                    if not ln or len(ln) < 2:
                        return False
                    low = ln.lower()
                    if any(low.startswith(p) or p in low[:40] for p in noise_prefixes):
                        return False
                    if re.match(r"^\d{1,2}[-./]\d{1,2}[-./]\d{2,4}\s+से", ln):
                        return False  # Hindi date range header
                    return True

                rows: list[dict] = []
                prev_balance: float | None = None
                # Pre-compute all indices of ROW_RE-matching lines so we can find where
                # a description body ends without spilling into the next row's prefix.
                row_indices = [i for i, ln in enumerate(all_lines) if ROW_RE.match(ln)]
                for ri, i in enumerate(row_indices):
                    ln = all_lines[i]
                    m = ROW_RE.match(ln)
                    if not m:
                        continue
                    sr, txn_date, middle_str, bal_str = m.groups()
                    balance = normalize_amount(bal_str)
                    # Middle tokens: could be [debit, credit] with '-' placeholders, or [amount].
                    tokens = [t for t in re.split(r"\s+", middle_str.strip()) if NUM_TOKEN.match(t)]
                    debit = 0.0
                    credit = 0.0
                    if len(tokens) >= 2:
                        # Standard bank layout: debit column then credit column, one is '-'
                        d_raw = tokens[-2]
                        c_raw = tokens[-1]
                        debit = normalize_amount(d_raw) if d_raw != "-" else 0.0
                        credit = normalize_amount(c_raw) if c_raw != "-" else 0.0
                    elif len(tokens) == 1:
                        # Single-amount layout (SBI): direction inferred from balance delta.
                        amt = normalize_amount(tokens[0])
                        if prev_balance is None:
                            debit = amt  # first row — assume debit; will self-correct
                        elif balance > prev_balance + 0.01:
                            credit = amt
                        else:
                            debit = amt
                    if debit <= 0 and credit <= 0:
                        prev_balance = balance
                        continue
                    # Description assembly — for SBI-style formats the merchant NAME is
                    # on the line RIGHT BEFORE the row (e.g. "GURWINDER" then "1 28.02.2026 500 ...").
                    # For BoB-style formats the description wraps ABOVE (UPI/ref/time/...) and
                    # a stray tail-letter (like "n" from "Sent") appears BELOW.
                    # Rule: description = [prev few lines that aren't rows/headers] + [lines
                    # between this row and next row], excluding the line RIGHT BEFORE the next
                    # row (which is the next row's name-prefix).
                    next_row_i = row_indices[ri + 1] if ri + 1 < len(row_indices) else len(all_lines)
                    prev_row_i = row_indices[ri - 1] if ri > 0 else -1
                    desc_parts: list[str] = []
                    # "Above": only the single line right BEFORE this row (the merchant
                    # name-prefix in SBI-style formats). Do NOT pull earlier lines — those
                    # belong to the PREVIOUS row's tail description.
                    if i - 1 > prev_row_i:
                        cand = all_lines[i - 1]
                        if _is_desc_line(cand) and not ROW_RE.match(cand):
                            desc_parts.append(cand)
                    # "Below": every line strictly between this row and the next row,
                    # EXCLUDING the single line right before the next row (which is that
                    # row's name-prefix). Use next_row_i - 1 as the stop.
                    tail_stop = next_row_i - 1 if next_row_i - 1 > i else next_row_i
                    for k in range(i + 1, tail_stop):
                        if k >= len(all_lines):
                            break
                        cand = all_lines[k]
                        if ROW_RE.match(cand):
                            break
                        if _is_desc_line(cand):
                            desc_parts.append(cand)
                    desc = " ".join(desc_parts)
                    # Clean up mixed column bleed / stray tail letters
                    desc = re.sub(r"\s+", " ", desc).strip(" -\t|")
                    if not desc:
                        desc = f"Bank transaction {sr}"
                    upi_ref_m = re.search(r"/(\d{9,22})/", desc)
                    upi_id_m = re.search(r"([\w.\-]+@[\w\-]+)", desc)
                    if credit > 0:
                        amount = credit; txn_type = "Income"
                    else:
                        amount = debit; txn_type = "Expense"
                    txn_type, category = resolve_type_and_category(desc, txn_type, None)
                    txn = {
                        "id": str(uuid.uuid4()),
                        "date": normalize_date(txn_date),
                        "description": desc[:120],
                        "amount": round(amount, 2),
                        "type": txn_type,
                        "category": category,
                        "source": source,
                    }
                    if upi_ref_m:
                        txn["upi_ref"] = upi_ref_m.group(1)
                    if upi_id_m:
                        txn["upi_id"] = upi_id_m.group(1)
                    rows.append(txn)
                    prev_balance = balance
                if rows:
                    transactions.extend(rows)
                    meta["text_fallback_used"] = True
                    meta["bank_row_parser_used"] = True

            # --- Strategy 2: text fallback (only if tables gave us NOTHING useful) ---
            if not transactions:
                meta["text_fallback_used"] = True
                if meta["tables_seen"] == 0:
                    meta["warnings"].append(
                        "No tabular structure found — this looks like a scanned or image-only PDF. "
                        "Falling back to text extraction; some rows may be missed."
                    )
                lines: list[str] = []
                for page in pdf.pages:
                    text = page.extract_text() or ""
                    lines.extend([ln.strip() for ln in text.splitlines() if ln.strip()])
                for line in lines:
                    if not _row_looks_like_txn(line):
                        continue
                    date_match = None
                    for pat in DATE_PATTERNS:
                        m = re.search(pat, line)
                        if m:
                            date_match = m
                            break
                    if not date_match:
                        continue
                    remainder = line[date_match.end():].strip()
                    amounts = re.findall(AMOUNT_PATTERN, remainder)
                    amounts = [a for a in amounts if any(c.isdigit() for c in a) and normalize_amount(a) > 0]
                    if not amounts:
                        continue
                    # If there are 2+ amounts on the line, treat the LAST as running balance and
                    # the PREVIOUS as the txn amount. (Statements almost always print Balance last.)
                    txn_amount_str = amounts[-2] if len(amounts) >= 2 else amounts[-1]
                    amount_value = normalize_amount(txn_amount_str)
                    if amount_value <= 0:
                        continue
                    desc = remainder
                    for a in amounts:
                        desc = desc.replace(a, "")
                    hint = None
                    if CREDIT_RE.search(line):
                        hint = "credit"
                    elif DEBIT_RE.search(line):
                        hint = "debit"
                    desc = CREDIT_RE.sub("", desc)
                    desc = DEBIT_RE.sub("", desc)
                    desc = re.sub(r"\s+", " ", desc).strip(" -\t|")
                    if not desc or len(desc) < 3:
                        continue
                    txn_type, absolute_amount = _detect_type(amount_value, hint)
                    if absolute_amount <= 0:
                        continue
                    txn_type, category = resolve_type_and_category(desc, txn_type, None)
                    txn = {
                        "id": str(uuid.uuid4()),
                        "date": normalize_date(date_match.group(1)),
                        "description": desc[:120],
                        "amount": round(absolute_amount, 2),
                        "type": txn_type,
                        "category": category,
                        "source": source,
                    }
                    if source == "upi":
                        upi_meta = extract_upi_meta(desc)
                        txn.update({k: v for k, v in upi_meta.items() if v})
                        app_key = detect_upi_app(desc)
                        if app_key:
                            txn["upi_app"] = app_key
                    transactions.append(txn)
                if lines and not sample_snippet:
                    sample_snippet = "\n".join(lines[:6])
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Couldn't read this PDF: {exc}") from exc

    # De-duplicate exact repeats (page footers can print the same row twice)
    seen: set = set()
    unique: list[dict] = []
    for t in transactions:
        k = (t.get("date"), round(float(t.get("amount", 0)), 2), t.get("description", "")[:60])
        if k in seen:
            continue
        seen.add(k)
        unique.append(t)
    # Warning heuristic: if we processed 3+ pages but found only a handful of
    # rows, something upstream is wrong (encrypted PDF, scanned image, or the
    # bank uses a very unusual layout). Surface it to the user before import.
    if meta["pages_processed"] >= 3 and len(unique) < meta["pages_processed"]:
        meta["warnings"].append(
            f"Only {len(unique)} transaction(s) extracted from {meta['pages_processed']} pages — "
            "the file may be scanned/image-based or use a layout we don't recognise. "
            "If it's a scanned PDF, export a fresh CSV/XLSX from your bank's portal."
        )
    return unique, sample_snippet, meta


# -------- FastAPI router --------

ALLOWED_CATEGORIES = {
    "Income", "Food", "Shopping", "Transport", "Rent", "Bills", "Education",
    "Entertainment", "Healthcare", "Other",
    "Miscellaneous Credit", "Miscellaneous Debit",
    "Internal Transfer",
}
ALLOWED_TYPES = {"Income", "Expense"}
ALLOWED_SOURCES = {"bank", "upi"}


# -------- AI-powered review pass (auto-categorize + sanity-check) --------

def _rule_based_ai_review(transactions: list[dict]) -> list[dict]:
    """Deterministic fallback: clean up mangled descriptions, resolve type/category
    without an LLM. Used when EMERGENT_LLM_KEY is missing or the LLM call fails."""
    out = []
    for t in transactions:
        desc = str(t.get("description") or "").strip()
        # If description degenerated to a stop-word remnant like just 'to' / 'from',
        # try to recover context from merchant / upi_id fields.
        if len(desc) < 4 or desc.lower() in {"to", "from", "paid", "sent", "received"}:
            recover = (t.get("merchant") or t.get("upi_id") or "").strip()
            if recover:
                desc = recover
            else:
                desc = "Uncategorized transaction"
        txn_type = t.get("type") if t.get("type") in ALLOWED_TYPES else "Expense"
        # Force sign-of-amount handling to positive
        try:
            amt = abs(float(t.get("amount") or 0))
        except (TypeError, ValueError):
            amt = 0.0
        cat_source = f"{t.get('merchant') or ''} {desc}".strip()
        txn_type, category = resolve_type_and_category(cat_source, txn_type, t.get("category"))
        # If income-side row still ended up on a debit category (or vice versa), coerce.
        if txn_type == "Expense" and category == "Income":
            category = guess_category(cat_source, "Expense") or "Miscellaneous Debit"
        if txn_type == "Income" and category not in ("Income", "Miscellaneous Credit", "Internal Transfer"):
            category = "Income"
        cleaned = {**t, "description": desc[:120], "type": txn_type,
                   "category": category if category in ALLOWED_CATEGORIES else "Other",
                   "amount": round(amt, 2), "ai_reviewed": False}
        out.append(cleaned)
    return out


async def ai_review_transactions(transactions: list[dict], source: str = "bank") -> tuple[list[dict], bool, str]:
    """Ask Claude Sonnet 5 to double-check each parsed transaction: fix mangled
    descriptions, pick the best Category, and confirm Type. Returns
    (cleaned_transactions, ai_used, note). Rule-based fallback if no key/LLM error."""
    if not transactions:
        return [], False, "empty"
    # Rule-based first — LLM only enriches confident output.
    baseline = _rule_based_ai_review(transactions)
    key = os.environ.get("EMERGENT_LLM_KEY")
    if not key:
        return baseline, False, "no_llm_key"
    try:
        from emergentintegrations.llm.chat import LlmChat, UserMessage
    except Exception:
        return baseline, False, "no_llm_lib"

    # Keep the prompt compact — send only the fields Claude needs.
    slim = [{
        "i": idx,
        "date": t.get("date"),
        "description": t.get("description"),
        "merchant": t.get("merchant"),
        "amount": t.get("amount"),
        "type": t.get("type"),
        "category": t.get("category"),
    } for idx, t in enumerate(baseline)]

    system = (
        "You are FINAURA AI's statement reviewer. You are given a list of transactions "
        "extracted from a bank or UPI statement. For each transaction, do THREE things: "
        "(1) if the description is mangled or too short (e.g. just 'to' or 'from'), rewrite "
        "it into a short human-readable label using the merchant field or any brand you can "
        "recognise from the raw text; keep it under 60 characters. "
        "(2) pick the CORRECT type — 'Expense' when the money leaves the user, 'Income' when "
        "it comes in (salary, refund, cashback, interest, dividend, received-from). "
        "(3) assign the best category from this fixed list: Income, Food, Shopping, Transport, "
        "Rent, Bills, Education, Entertainment, Healthcare, Other, Miscellaneous Credit, "
        "Miscellaneous Debit, Internal Transfer. Rules: Type=Income MUST get category Income "
        "(or Miscellaneous Credit / Internal Transfer). Type=Expense must NEVER get category "
        "Income. Groceries -> Shopping. Metro/Uber/Ola/petrol -> Transport. Electricity/mobile/"
        "recharge/DTH/broadband -> Bills. Amazon/Flipkart/Myntra -> Shopping. Swiggy/Zomato/"
        "restaurants -> Food. Netflix/Prime/Spotify/BookMyShow -> Entertainment. Doctor/hospital/"
        "pharmacy/apollo -> Healthcare. Rent/landlord -> Rent. Fees/school/course/udemy -> "
        "Education. Reply with STRICT JSON only — no prose, no code fences."
    )
    prompt = (
        f"Source: {source}. Transactions ({len(slim)}): {_json_mod.dumps(slim)}\n\n"
        'JSON schema: {"items": [{"i": 0, "description": "...", "type": "Expense|Income", '
        '"category": "one of the allowed values"}, ...]}'
    )
    try:
        chat = LlmChat(
            api_key=key,
            session_id=f"stmt-review-{int(datetime.now().timestamp())}",
            system_message=system,
        ).with_model("anthropic", "claude-sonnet-5")
        resp = await chat.send_message(UserMessage(text=prompt))
        text = getattr(resp, "text", None) or str(resp)
        s = text.strip()
        if s.startswith("```"):
            s = s.strip("`")
            if s.lower().startswith("json"):
                s = s[4:]
            s = s.strip()
        first = s.find("{"); last = s.rfind("}")
        if first < 0 or last < 0:
            raise ValueError("no json object")
        parsed = _json_mod.loads(s[first : last + 1])
        items = parsed.get("items") or []
        by_index = {int(x.get("i", -1)): x for x in items if isinstance(x, dict)}
        result = []
        for idx, base in enumerate(baseline):
            override = by_index.get(idx) or {}
            new_desc = str(override.get("description") or base.get("description") or "").strip()[:120]
            new_type = override.get("type") if override.get("type") in ALLOWED_TYPES else base.get("type")
            new_cat = override.get("category") if override.get("category") in ALLOWED_CATEGORIES else base.get("category")
            # Sanity: never allow Expense with category Income (or vice versa) even if LLM slips.
            if new_type == "Expense" and new_cat == "Income":
                new_cat = "Miscellaneous Debit"
            if new_type == "Income" and new_cat not in ("Income", "Miscellaneous Credit", "Internal Transfer"):
                new_cat = "Income"
            result.append({**base, "description": new_desc, "type": new_type,
                           "category": new_cat, "ai_reviewed": True})
        return result, True, "ok"
    except Exception as exc:
        log.warning("ai_review_transactions LLM error: %s", exc)
        return baseline, False, f"llm_error"


# Words that indicate a transfer that must NOT be counted as income
INTERNAL_TRANSFER_HINTS = ("self transfer", "own account", "linked account", "credit card payment",
                          "cc payment", "sweep", "auto sweep", "transfer to self",
                          "imps to self", "neft to self")


class ConfirmImportInput(BaseModel):
    transactions: list[dict]
    source: str | None = "bank"
    file_name: str | None = None


class ResolveDuplicateInput(BaseModel):
    keep_id: str
    drop_id: str


class VerifyInput(BaseModel):
    month: str | None = None  # "Feb 2026" — optional; None = all months


class AiReviewInput(BaseModel):
    transactions: list[dict]
    source: str | None = "bank"


def _txn_month(txn: dict) -> str:
    """Return 'Mon YYYY' bucket for a transaction using its date string."""
    date_str = str(txn.get("date") or "")
    for fmt in ("%d %b %Y", "%d %B %Y", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(date_str, fmt).strftime("%b %Y")
        except ValueError:
            continue
    try:
        return pd.to_datetime(date_str, dayfirst=True).strftime("%b %Y")
    except Exception:
        return "Unknown"


def _txn_datetime(txn: dict) -> datetime | None:
    date_str = str(txn.get("date") or "")
    for fmt in ("%d %b %Y", "%d %B %Y", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    try:
        return pd.to_datetime(date_str, dayfirst=True).to_pydatetime()
    except Exception:
        return None


def _match_score(bank: dict, upi: dict) -> tuple[float, str]:
    """Return (score 0..1, reason). Higher = more confident match."""
    if bank.get("type") != upi.get("type"):
        return 0.0, "type mismatch"
    a1 = round(float(bank.get("amount", 0)), 2)
    a2 = round(float(upi.get("amount", 0)), 2)
    if abs(a1 - a2) > 0.01:
        return 0.0, "amount mismatch"
    d1 = _txn_datetime(bank); d2 = _txn_datetime(upi)
    if not d1 or not d2:
        return 0.0, "date parse failed"
    delta = abs((d1 - d2).days)
    if delta > 3:
        return 0.0, f"date too far ({delta}d)"
    # Extract UPI reference / txn id from BOTH sides (bank may carry it in narration).
    upi_ref = (upi.get("upi_ref") or upi.get("txn_id") or "").strip()
    bank_desc = (bank.get("description") or "")
    # Pull a 9-22 digit run out of the bank narration if present.
    m = re.search(r"\b(\d{9,22})\b", bank_desc)
    bank_ref = m.group(1) if m else ""
    # Hard veto: if BOTH sides carry a distinct reference id, they are different
    # transactions — never auto-merge. This prevents silent expense deletion.
    if upi_ref and bank_ref and upi_ref != bank_ref:
        return 0.0, f"ref mismatch ({bank_ref} vs {upi_ref})"
    score = 0.6  # amount + type + close date
    if delta == 0:
        score += 0.15
    elif delta <= 2:
        score += 0.05  # small bonus for a very close date
    # Positive: UPI ref or transaction id match in bank narration
    if upi_ref and upi_ref in bank_desc:
        score += 0.25
        return min(score, 1.0), f"ref {upi_ref} present"
    # Merchant / description overlap — must be a MEANINGFUL match to justify auto-merge.
    # Generic verbs / connectives are excluded so 'Paid Wwww' vs 'Paid Yyyy' does not merge.
    STOP = {
        "upi", "payment", "credit", "debit", "transfer", "ref", "txn", "no", "id",
        "order", "the", "and", "paid", "sent", "received", "receive", "to", "from",
        "via", "bill", "for", "of", "on", "at", "by", "with", "money",
    }
    def _tokens(s: str) -> set:
        return {t for t in re.split(r"[^a-z0-9]+", (s or "").lower()) if len(t) > 2 and t not in STOP}
    bank_tokens = _tokens(bank_desc)
    upi_desc_tokens = _tokens(upi.get("description", ""))
    upi_merch_tokens = _tokens(upi.get("merchant", ""))
    upi_tokens = upi_desc_tokens | upi_merch_tokens
    overlap = bank_tokens & upi_tokens
    # Strong signal: the UPI merchant name (distinctive, non-generic) fully appears
    # in the bank narration — this is the classic "UPI SWIGGY BLR" vs merchant="Swiggy"
    # dedupe case. If the UPI row has no dedicated merchant column, fall back to the
    # UPI description tokens (which is what payment apps like PhonePe show as the payee).
    upi_name_tokens = upi_merch_tokens or upi_desc_tokens
    merchant_match = bool(upi_name_tokens) and upi_name_tokens.issubset(bank_tokens)
    if merchant_match:
        score += 0.25
    elif len(overlap) >= 2:
        score += 0.25
    elif len(overlap) == 1:
        score += 0.10
    # 'upi' presence in bank narration is a nudge, not a requirement
    if "upi" in bank_desc.lower():
        score += 0.05
    return min(score, 1.0), f"heuristic ({delta}d, overlap={sorted(overlap)[:2]})"


def cross_verify(bank_txns: list[dict], upi_txns: list[dict]) -> dict:
    """Match bank transactions against UPI transactions and classify each.
    Returns lists suitable for the frontend verification view."""
    matched_bank_ids: set = set()
    verified_bank_ids: set = set()
    matched_upi_ids: set = set()
    verified: list[dict] = []
    possible: list[dict] = []
    for u in upi_txns:
        u_id = u.get("id")
        if not u_id:
            continue
        best = None
        best_score = 0.0
        best_reason = ""
        for b in bank_txns:
            b_id = b.get("id")
            if not b_id or b_id in matched_bank_ids:
                continue
            score, reason = _match_score(b, u)
            if score > best_score:
                best = b; best_score = score; best_reason = reason
        if best is None:
            continue
        b_id = best.get("id")
        if best_score >= 0.85:
            verified.append({
                "upi_txn": _strip_txn(u), "bank_txn": _strip_txn(best), "score": round(best_score, 2),
                "reason": best_reason, "status": "verified",
            })
            matched_bank_ids.add(b_id)
            verified_bank_ids.add(b_id)
            matched_upi_ids.add(u_id)
        elif best_score >= 0.6:
            possible.append({
                "upi_txn": _strip_txn(u), "bank_txn": _strip_txn(best), "score": round(best_score, 2),
                "reason": best_reason, "status": "possible",
            })
            matched_bank_ids.add(b_id)
            matched_upi_ids.add(u_id)
    upi_only = [_strip_txn(u) for u in upi_txns if u.get("id") not in matched_upi_ids]
    bank_only = [_strip_txn(b) for b in bank_txns if b.get("id") not in matched_bank_ids]
    return {
        "verified_matches": verified,
        "possible_matches": possible,
        "upi_only": upi_only,
        "bank_only": bank_only,
        # Internal — used by dedupe_across_sources; stripped from public /verify response.
        # Only VERIFIED matches (>=0.85) are safe to dedupe automatically.
        "verified_bank_ids": list(verified_bank_ids),
        "counts": {
            "bank_total": len(bank_txns),
            "upi_total": len(upi_txns),
            "verified": len(verified),
            "possible": len(possible),
            "upi_only": len(upi_only),
            "bank_only": len(bank_only),
        },
    }


def _strip_txn(t: dict) -> dict:
    """Return a client-safe copy of a transaction (no Mongo _id / user_id / audit fields)."""
    return {k: v for k, v in t.items() if k not in ("_id", "user_id", "created_at", "verified_at")}


def dedupe_across_sources(transactions: list[dict]) -> list[dict]:
    """Return transactions with cross-source duplicates removed. Only VERIFIED matches
    (score >= 0.85) are auto-deduped so we never silently drop an unrelated same-amount
    payment that just happens to fall within 3 days. Possible matches (0.6-0.85) stay
    in analytics and only surface in the /verify view for the user to review."""
    bank = [t for t in transactions if t.get("source", "bank") == "bank"]
    upi = [t for t in transactions if t.get("source") == "upi"]
    if not upi:
        return transactions
    result = cross_verify(bank, upi)
    drop_bank_ids = set(result.get("verified_bank_ids") or [])
    return [t for t in transactions if not (t.get("source", "bank") == "bank" and t.get("id") in drop_bank_ids)]


def _find_verified_match(new_txn: dict, existing_other: list[dict]) -> dict | None:
    """Find the best VERIFIED match (score >= 0.85) for `new_txn` in `existing_other`.
    Returns the existing row so the caller can attach source linkage without inserting
    a duplicate row into the ledger."""
    best = None
    best_score = 0.0
    # cross_verify expects (bank, upi). Orient by source.
    if new_txn.get("source") == "upi":
        for b in existing_other:
            score, _ = _match_score(b, new_txn)
            if score > best_score:
                best = b; best_score = score
    else:
        for u in existing_other:
            score, _ = _match_score(new_txn, u)
            if score > best_score:
                best = u; best_score = score
    return best if best_score >= 0.85 else None


# -------- Extraction summary helper --------

def _extraction_summary(transactions: list[dict], pages_processed: int | None = None,
                        warnings: list[str] | None = None, extra: dict | None = None) -> dict:
    """Uniform summary the frontend shows on the Review step BEFORE import.
    Includes counts by direction, needs-review count, and warnings so the user
    can spot 'only 2 rows out of a 6-month statement' before saving."""
    credits = sum(1 for t in transactions if (t.get("type") or "").lower() == "income")
    debits = sum(1 for t in transactions if (t.get("type") or "").lower() == "expense")
    credit_total = sum(float(t.get("amount") or 0) for t in transactions if (t.get("type") or "").lower() == "income")
    debit_total = sum(float(t.get("amount") or 0) for t in transactions if (t.get("type") or "").lower() == "expense")
    needs_review = sum(1 for t in transactions
                       if (t.get("category") in {"Miscellaneous Credit", "Miscellaneous Debit"})
                       or not (t.get("description") or "").strip()
                       or (len((t.get("description") or "").strip()) < 4))
    warns = list(warnings or [])
    if not transactions:
        warns.append("No transactions were detected. Check the column mapping (CSV/Excel) or export a fresh CSV from your bank if this is a scanned PDF.")
    summary = {
        "transactions_detected": len(transactions),
        "credits_count": credits,
        "debits_count": debits,
        "credits_total": round(credit_total, 2),
        "debits_total": round(debit_total, 2),
        "needs_review": needs_review,
        "warnings": warns,
    }
    if pages_processed is not None:
        summary["pages_processed"] = pages_processed
    if extra:
        summary.update(extra)
    return summary


def build_statements_router(db: AsyncIOMotorDatabase, get_current_user):
    router = APIRouter(prefix="/statements", tags=["statements"])

    @router.post("/preview")
    async def preview(
        file: UploadFile = File(...),
        source: str = Form("bank"),
        user=Depends(get_current_user),
    ):
        source = source if source in ALLOWED_SOURCES else "bank"
        raw = await file.read()
        if not raw:
            raise HTTPException(status_code=400, detail="Empty file.")
        if len(raw) > 10 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="File is larger than 10 MB.")
        name = (file.filename or "").lower()
        if name.endswith(".csv") or (file.content_type or "").startswith("text/csv"):
            try:
                info = csv_preview(raw, source=source)
            except Exception as exc:
                raise HTTPException(status_code=400, detail=f"Couldn't read this CSV: {exc}") from exc
            info["extraction_summary"] = _extraction_summary(
                [], warnings=[],
                extra={"columns_seen": len(info.get("columns") or []), "row_estimate": info.get("total_rows", 0)},
            )
            return {"kind": "csv", "source": source, **info}
        if name.endswith(".xlsx") or name.endswith(".xls"):
            try:
                info = excel_preview(raw, source=source)
            except Exception as exc:
                raise HTTPException(status_code=400, detail=f"Couldn't read this spreadsheet: {exc}") from exc
            info["extraction_summary"] = _extraction_summary(
                [], warnings=[],
                extra={"columns_seen": len(info.get("columns") or []), "row_estimate": info.get("total_rows", 0)},
            )
            return {"kind": "excel", "source": source, **info}
        if name.endswith(".pdf") or (file.content_type or "") == "application/pdf":
            transactions, sample, meta = parse_pdf(raw, source=source)
            summary = _extraction_summary(transactions, pages_processed=meta.get("pages_processed"),
                                          warnings=meta.get("warnings"),
                                          extra={"tables_seen": meta.get("tables_seen"),
                                                 "text_fallback_used": meta.get("text_fallback_used")})
            return {"kind": "pdf", "source": source, "transactions": transactions,
                    "sample_lines": sample, "extraction_summary": summary}
        raise HTTPException(status_code=415, detail="Unsupported file. Please upload CSV, Excel, or PDF.")

    @router.post("/parse")
    async def parse(
        file: UploadFile = File(...),
        mapping: str = Form("{}"),
        source: str = Form("bank"),
        user=Depends(get_current_user),
    ):
        """Return transactions extracted with the provided column mapping (CSV/Excel)
        or best-effort text extraction (PDF). Nothing is stored yet."""
        source = source if source in ALLOWED_SOURCES else "bank"
        import json as _json
        raw = await file.read()
        if not raw:
            raise HTTPException(status_code=400, detail="Empty file.")
        if len(raw) > 10 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="File is larger than 10 MB.")
        try:
            mapping_dict = _json.loads(mapping) if mapping else {}
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid mapping JSON.")
        name = (file.filename or "").lower()
        if name.endswith(".csv"):
            txns = parse_csv(raw, mapping_dict, source=source)
            row_estimate = 0
            warnings = []
            try:
                _prev = csv_preview(raw, source=source)
                row_estimate = _prev.get("total_rows", 0) or 0
            except Exception:
                pass
            if row_estimate and len(txns) < max(2, int(row_estimate * 0.3)):
                warnings.append(
                    f"Only {len(txns)} of ~{row_estimate} rows produced a transaction. "
                    "Double-check the column mapping — the Amount / Debit / Credit column may be pointing to the wrong field."
                )
            return {"transactions": txns, "source": source,
                    "extraction_summary": _extraction_summary(txns, warnings=warnings,
                                                              extra={"row_estimate": row_estimate})}
        if name.endswith(".xlsx") or name.endswith(".xls"):
            txns = parse_excel(raw, mapping_dict, source=source)
            return {"transactions": txns, "source": source,
                    "extraction_summary": _extraction_summary(txns)}
        if name.endswith(".pdf"):
            transactions, _sample, meta = parse_pdf(raw, source=source)
            return {"transactions": transactions, "source": source,
                    "extraction_summary": _extraction_summary(transactions,
                                                              pages_processed=meta.get("pages_processed"),
                                                              warnings=meta.get("warnings"),
                                                              extra={"tables_seen": meta.get("tables_seen"),
                                                                     "text_fallback_used": meta.get("text_fallback_used")})}
        raise HTTPException(status_code=415, detail="Unsupported file.")

    @router.post("/ai-review")
    async def ai_review(body: AiReviewInput, user=Depends(get_current_user)):
        """Second-pass AI review of parsed transactions before the user sees them.
        Cleans up mangled descriptions and picks the best Category so the user does
        NOT have to hand-tag every row. Falls back to a rule-based pass if the LLM
        is unavailable. Nothing is stored — the frontend then shows the cleaned list
        in the Review step, and users can still edit any row."""
        source = body.source if body.source in ALLOWED_SOURCES else "bank"
        # Guard: at most 200 transactions per call to keep prompt small.
        txns = list(body.transactions or [])[:200]
        cleaned, ai_used, note = await ai_review_transactions(txns, source=source)
        return {"transactions": cleaned, "ai_used": ai_used, "note": note}

    @router.post("/confirm-import")
    async def confirm_import(body: ConfirmImportInput, user=Depends(get_current_user)):
        uid = str(user["_id"])
        source = body.source if body.source in ALLOWED_SOURCES else "bank"
        file_name = (body.file_name or "").strip()[:120] or f"{source}-{datetime.now(timezone.utc).strftime('%d %b %Y %H:%M')}"
        statement_id = str(uuid.uuid4())

        # First-time real upload → nuke any pre-seeded demo rows so the ledger stays clean
        demo_rows = await db.finaura_transactions.count_documents({"user_id": uid, "source": "demo"})
        if demo_rows:
            await db.finaura_transactions.delete_many({"user_id": uid, "source": "demo"})
            await db.users.update_one({"_id": user["_id"]}, {"$set": {"has_demo_data": False}})

        # Pull existing OTHER-source transactions so we can merge cross-source duplicates on import
        other_source = "upi" if source == "bank" else "bank"
        existing_other = [t async for t in db.finaura_transactions.find({
            "user_id": uid, "source": other_source
        })]

        clean = []
        merged = 0
        for t in body.transactions:
            desc = str(t.get("description", "")).strip()
            try:
                amount = float(t.get("amount", 0))
            except (TypeError, ValueError):
                continue
            if not desc or amount <= 0:
                continue
            txn_type = t.get("type") if t.get("type") in ALLOWED_TYPES else "Expense"
            # Central classification — an internal transfer credit never becomes 'Income'
            cat_hint = t.get("merchant") or t.get("description") or ""
            txn_type, category = resolve_type_and_category(f"{cat_hint} {desc}", txn_type,
                                                          t.get("category") if t.get("category") in ALLOWED_CATEGORIES else None)

            candidate = {
                "id": str(uuid.uuid4()),
                "user_id": uid,
                "date": (t.get("date") or datetime.now(timezone.utc).strftime("%d %b %Y"))[:32],
                "description": desc[:120],
                "amount": round(amount, 2),
                "type": txn_type,
                "category": category,
                "source": source,
                "statement_id": statement_id,
                "file_name": file_name,
                "created_at": datetime.now(timezone.utc),
            }
            for k in ("upi_ref", "txn_id", "upi_id", "merchant", "upi_app"):
                v = t.get(k)
                if v:
                    candidate[k] = str(v)[:80]

            # Look for a VERIFIED match in the other source — if found, merge instead of inserting
            match = _find_verified_match(candidate, existing_other)
            if match:
                merged += 1
                await db.finaura_transactions.update_one(
                    {"_id": match["_id"]},
                    {"$set": {
                        "linked_txn_id": candidate["id"],
                        "linked_source": source,
                        "linked_statement_id": statement_id,
                        "verified": True,
                        "verified_at": datetime.now(timezone.utc),
                    }},
                )
                candidate["linked_txn_id"] = str(match.get("id"))
                candidate["linked_source"] = other_source
                candidate["linked_statement_id"] = match.get("statement_id")
                candidate["verified"] = True
                candidate["verified_at"] = datetime.now(timezone.utc)
                # Prevent this bank/upi row from also linking to another
                existing_other = [x for x in existing_other if x["_id"] != match["_id"]]
            clean.append(candidate)

        if clean:
            await db.finaura_transactions.insert_many(clean)
        return {
            "imported": len(clean),
            "merged": merged,
            "source": source,
            "statement_id": statement_id,
        }

    @router.get("/verify")
    async def verify(month: str | None = None, user=Depends(get_current_user)):
        """Cross-verify bank and UPI transactions for the user, optionally scoped to
        a single 'Mon YYYY' month string. Returns match categories used by the UI."""
        uid = str(user["_id"])
        all_txns = [t async for t in db.finaura_transactions.find({"user_id": uid}).limit(5000)]
        # Strip Mongo internals so they never leak to the client
        for t in all_txns:
            t.pop("_id", None)
            t.pop("user_id", None)
            t.pop("created_at", None)
            t.pop("verified_at", None)
        if month:
            all_txns = [t for t in all_txns if _txn_month(t) == month]
        bank = [t for t in all_txns if t.get("source", "bank") == "bank"]
        upi = [t for t in all_txns if t.get("source") == "upi"]
        result = cross_verify(bank, upi)
        # Don't leak internal helper field to clients
        result.pop("matched_bank_ids", None)
        result.pop("verified_bank_ids", None)
        # Per-month breakdown
        months: dict[str, dict] = {}
        for t in all_txns:
            m = _txn_month(t)
            entry = months.setdefault(m, {"month": m, "bank_count": 0, "upi_count": 0})
            entry["bank_count" if t.get("source", "bank") == "bank" else "upi_count"] += 1
        result["months"] = sorted(months.values(), key=lambda x: x["month"], reverse=True)
        return result

    @router.get("/master")
    async def master(user=Depends(get_current_user)):
        """Single source of truth for the dashboard: returns the deduped master
        ledger (bank ∪ UPI with cross-source duplicates merged), the cross-check
        counts (verified / possible / bank-only / upi-only), and per-source totals.
        The dashboard, insights, goals and AI all read from THIS view — never from
        raw bank + UPI sums, so a UPI payment linked to a bank card is counted once."""
        uid = str(user["_id"])
        all_txns = [t async for t in db.finaura_transactions.find({"user_id": uid}).limit(5000)]
        for t in all_txns:
            t.pop("_id", None)
            t.pop("user_id", None)
            t.pop("created_at", None)
        bank = [t for t in all_txns if t.get("source", "bank") == "bank"]
        upi = [t for t in all_txns if t.get("source") == "upi"]
        # Cross-verify to get status counts (never returned to client, only summaries)
        cv = cross_verify(bank, upi)
        # Deduped master ledger — same rule used by /financial/overview
        master_list = dedupe_across_sources(all_txns)
        income_sum = sum(float(t.get("amount") or 0) for t in master_list if t.get("type") == "Income")
        expense_sum = sum(float(t.get("amount") or 0) for t in master_list if t.get("type") == "Expense")
        # By source (post-dedupe) so the dashboard can show Bank vs UPI without double counting
        by_source: dict = {}
        for t in master_list:
            src = t.get("source", "bank")
            e = by_source.setdefault(src, {"count": 0, "income": 0.0, "expense": 0.0})
            e["count"] += 1
            if t.get("type") == "Income":
                e["income"] += float(t.get("amount") or 0)
            elif t.get("type") == "Expense":
                e["expense"] += float(t.get("amount") or 0)
        # Warnings the frontend can surface prominently
        warnings: list[str] = []
        if cv["counts"]["possible"] > 0:
            warnings.append(
                f"{cv['counts']['possible']} bank↔UPI pair(s) look like they may be the same transaction. "
                "Review them on the Cross-verification tab so they're counted once."
            )
        if bank and not upi:
            warnings.append("You've only uploaded a bank statement so far. Add your UPI statement to catch UPI-linked spending your bank narration hides.")
        elif upi and not bank:
            warnings.append("You've only uploaded a UPI statement so far. Add your bank statement to see salary, rent and card spending.")
        return {
            "master_count": len(master_list),
            "raw_count": len(all_txns),
            "removed_by_dedupe": len(all_txns) - len(master_list),
            "income_total": round(income_sum, 2),
            "expense_total": round(expense_sum, 2),
            "net": round(income_sum - expense_sum, 2),
            "by_source": {k: {"count": v["count"],
                              "income": round(v["income"], 2),
                              "expense": round(v["expense"], 2)} for k, v in by_source.items()},
            "cross_check": {
                "verified": cv["counts"]["verified"],
                "possible": cv["counts"]["possible"],
                "bank_only": cv["counts"]["bank_only"],
                "upi_only": cv["counts"]["upi_only"],
            },
            "warnings": warnings,
        }

    @router.post("/resolve-duplicate")
    async def resolve_duplicate(body: ResolveDuplicateInput, user=Depends(get_current_user)):
        """Deduplicate a verified match by deleting one side. The user chooses which
        (usually the bank side, since UPI has richer metadata)."""
        uid = str(user["_id"])
        keep_id = body.keep_id
        drop_id = body.drop_id
        if keep_id == drop_id:
            raise HTTPException(400, "keep_id and drop_id must differ.")
        # Verify both belong to this user before touching anything
        keep_doc = await db.finaura_transactions.find_one({"id": keep_id, "user_id": uid})
        drop_doc = await db.finaura_transactions.find_one({"id": drop_id, "user_id": uid})
        if not keep_doc or not drop_doc:
            raise HTTPException(404, "One or both transactions not found or not owned by you.")
        await db.finaura_transactions.delete_one({"id": drop_id, "user_id": uid})
        await db.finaura_transactions.update_one(
            {"id": keep_id, "user_id": uid},
            {"$set": {"verified": True, "verified_at": datetime.now(timezone.utc)}},
        )
        return {"deleted": 1, "verified": keep_id}

    return router
