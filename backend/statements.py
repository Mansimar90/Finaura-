"""Statement upload & parser — CSV, Excel, PDF text extraction."""
from __future__ import annotations

import io
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


# -------- PDF parsing (best effort text pattern extraction) --------

DATE_PATTERNS = [
    r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\b",
    r"\b(\d{1,2}\s+[A-Za-z]{3,9}\s+\d{2,4})\b",
    r"\b(\d{4}-\d{2}-\d{2})\b",
]
AMOUNT_PATTERN = r"([₹]?\s?[+-]?\s?\d{1,3}(?:,\d{2,3})+(?:\.\d{1,2})?|[₹]?\s?[+-]?\s?\d+(?:\.\d{1,2})?)"


DEBIT_RE = re.compile(r"\b(dr|debit|withdrawal|withdraw)\b", re.IGNORECASE)
CREDIT_RE = re.compile(r"\b(cr|credit|deposit)\b", re.IGNORECASE)

def parse_pdf(content: bytes, source: str = "bank") -> tuple[list[dict], str]:
    try:
        import pdfplumber
    except ImportError as exc:
        raise HTTPException(status_code=503, detail="PDF parser not available on this server.") from exc
    lines: list[str] = []
    try:
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            for page in pdf.pages:
                text = page.extract_text() or ""
                lines.extend([ln.strip() for ln in text.splitlines() if ln.strip()])
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Couldn't read this PDF: {exc}") from exc
    transactions: list[dict] = []
    for line in lines:
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
        amounts = [a for a in amounts if any(c.isdigit() for c in a)]
        if not amounts:
            continue
        raw_amount = amounts[-1]
        amount_value = normalize_amount(raw_amount)
        desc = remainder
        for a in amounts:
            desc = desc.replace(a, "")
        # word-boundary DR/CR detection anywhere in the line (including end)
        hint = None
        if CREDIT_RE.search(line):
            hint = "credit"
        elif DEBIT_RE.search(line):
            hint = "debit"
        # strip the DR/CR token(s) from the description
        desc = CREDIT_RE.sub("", desc)
        desc = DEBIT_RE.sub("", desc)
        desc = re.sub(r"\s+", " ", desc).strip(" -\t")
        if not desc:
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
    return transactions, "\n".join(lines[:6])


# -------- FastAPI router --------

ALLOWED_CATEGORIES = {
    "Income", "Food", "Shopping", "Transport", "Rent", "Bills", "Education",
    "Entertainment", "Healthcare", "Other",
    "Miscellaneous Credit", "Miscellaneous Debit",
    "Internal Transfer",
}
ALLOWED_TYPES = {"Income", "Expense"}
ALLOWED_SOURCES = {"bank", "upi"}


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
            return {"kind": "csv", "source": source, **info}
        if name.endswith(".xlsx") or name.endswith(".xls"):
            try:
                info = excel_preview(raw, source=source)
            except Exception as exc:
                raise HTTPException(status_code=400, detail=f"Couldn't read this spreadsheet: {exc}") from exc
            return {"kind": "excel", "source": source, **info}
        if name.endswith(".pdf") or (file.content_type or "") == "application/pdf":
            transactions, sample = parse_pdf(raw, source=source)
            return {"kind": "pdf", "source": source, "transactions": transactions, "sample_lines": sample}
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
            return {"transactions": parse_csv(raw, mapping_dict, source=source), "source": source}
        if name.endswith(".xlsx") or name.endswith(".xls"):
            return {"transactions": parse_excel(raw, mapping_dict, source=source), "source": source}
        if name.endswith(".pdf"):
            transactions, _ = parse_pdf(raw, source=source)
            return {"transactions": transactions, "source": source}
        raise HTTPException(status_code=415, detail="Unsupported file.")

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
