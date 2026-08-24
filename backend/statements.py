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
    if txn_type == "Income":
        return "Income"
    d = (description or "").lower()
    for cat, kws in CATEGORY_KEYWORDS.items():
        if any(k in d for k in kws):
            return cat
    return "Other"


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
    def find(*names):
        for n in names:
            for k, v in lower.items():
                if n in k:
                    return v
        return None
    base = {
        "date": find("date", "txn date", "value date", "posting", "transaction time", "time"),
        "description": find("description", "narration", "particulars", "details", "remarks", "note", "to / from", "merchant"),
        "amount": find("amount", "value", "txn amount"),
        "debit": find("debit", "withdrawal", "dr"),
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
            # UPI heuristic: positive-only amount + no type hint + merchant column → treat as an Expense (payment sent)
            if source == "upi" and not type_hint and amount > 0 and mapping.get("merchant"):
                txn_type = "Expense"
        elif mapping.get("debit") or mapping.get("credit"):
            dr = normalize_amount(row.get(mapping.get("debit"))) if mapping.get("debit") else 0
            cr = normalize_amount(row.get(mapping.get("credit"))) if mapping.get("credit") else 0
            if cr:
                txn_type = "Income"; amount = abs(cr)
            elif dr:
                txn_type = "Expense"; amount = abs(dr)
        if amount <= 0 or not str(desc_val).strip():
            continue
        desc = str(desc_val).strip()[:120]
        cat_source = desc
        if source == "upi" and mapping.get("merchant"):
            m_val = str(row.get(mapping.get("merchant"), "") or "").strip()
            if m_val:
                cat_source = f"{m_val} {desc}"
        txn = {
            "id": str(uuid.uuid4()),
            "date": normalize_date(date_val),
            "description": desc,
            "amount": round(amount, 2),
            "type": txn_type,
            "category": guess_category(cat_source, txn_type),
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
        txn = {
            "id": str(uuid.uuid4()),
            "date": normalize_date(date_match.group(1)),
            "description": desc[:120],
            "amount": round(absolute_amount, 2),
            "type": txn_type,
            "category": guess_category(desc, txn_type),
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

ALLOWED_CATEGORIES = {"Income", "Food", "Shopping", "Transport", "Rent", "Bills", "Education", "Entertainment", "Healthcare", "Other"}
ALLOWED_TYPES = {"Income", "Expense"}
ALLOWED_SOURCES = {"bank", "upi"}


class ConfirmImportInput(BaseModel):
    transactions: list[dict]
    source: str | None = "bank"


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
    score = 0.6  # amount + type + close date
    if delta == 0:
        score += 0.15
    # UPI ref or transaction id match in either description
    ref = (upi.get("upi_ref") or upi.get("txn_id") or "").strip()
    if ref and ref in (bank.get("description") or ""):
        score += 0.25
        return min(score, 1.0), f"ref {ref} present"
    # Description overlap
    desc1 = (bank.get("description") or "").lower()
    desc2 = (upi.get("description") or "").lower()
    tokens1 = {t for t in re.split(r"[^a-z0-9]+", desc1) if len(t) > 3}
    tokens2 = {t for t in re.split(r"[^a-z0-9]+", desc2) if len(t) > 3}
    if tokens1 & tokens2:
        score += 0.15
    # UPI keyword in bank description
    if "upi" in desc1 and delta <= 2:
        score += 0.1
    return min(score, 1.0), f"heuristic match ({delta}d)"


def cross_verify(bank_txns: list[dict], upi_txns: list[dict]) -> dict:
    """Match bank transactions against UPI transactions and classify each.
    Returns lists suitable for the frontend verification view."""
    matched_bank_ids: set = set()
    matched_upi_ids: set = set()
    verified: list[dict] = []
    possible: list[dict] = []
    # Prefer high-confidence matches first
    for u in upi_txns:
        best = None
        best_score = 0.0
        best_reason = ""
        for b in bank_txns:
            if b["id"] in matched_bank_ids:
                continue
            score, reason = _match_score(b, u)
            if score > best_score:
                best = b; best_score = score; best_reason = reason
        if best is None:
            continue
        if best_score >= 0.85:
            verified.append({
                "upi_txn": u, "bank_txn": best, "score": round(best_score, 2),
                "reason": best_reason, "status": "verified",
            })
            matched_bank_ids.add(best["id"])
            matched_upi_ids.add(u["id"])
        elif best_score >= 0.6:
            possible.append({
                "upi_txn": u, "bank_txn": best, "score": round(best_score, 2),
                "reason": best_reason, "status": "possible",
            })
            matched_bank_ids.add(best["id"])
            matched_upi_ids.add(u["id"])
    upi_only = [u for u in upi_txns if u["id"] not in matched_upi_ids]
    bank_only = [b for b in bank_txns if b["id"] not in matched_bank_ids]
    return {
        "verified_matches": verified,
        "possible_matches": possible,
        "upi_only": upi_only,
        "bank_only": bank_only,
        "counts": {
            "bank_total": len(bank_txns),
            "upi_total": len(upi_txns),
            "verified": len(verified),
            "possible": len(possible),
            "upi_only": len(upi_only),
            "bank_only": len(bank_only),
        },
    }


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
        clean = []
        for t in body.transactions:
            desc = str(t.get("description", "")).strip()
            try:
                amount = float(t.get("amount", 0))
            except (TypeError, ValueError):
                continue
            if not desc or amount <= 0:
                continue
            txn_type = t.get("type") if t.get("type") in ALLOWED_TYPES else "Expense"
            category = t.get("category") if t.get("category") in ALLOWED_CATEGORIES else "Other"
            doc = {
                "id": str(uuid.uuid4()),
                "user_id": uid,
                "date": (t.get("date") or datetime.now(timezone.utc).strftime("%d %b %Y"))[:32],
                "description": desc[:120],
                "amount": round(amount, 2),
                "type": txn_type,
                "category": category,
                "source": source,
                "created_at": datetime.now(timezone.utc),
            }
            # Persist UPI metadata if the frontend sent it
            for k in ("upi_ref", "txn_id", "upi_id", "merchant", "upi_app"):
                v = t.get(k)
                if v:
                    doc[k] = str(v)[:80]
            clean.append(doc)
        if clean:
            await db.finaura_transactions.insert_many(clean)
        return {"imported": len(clean), "source": source}

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
        # Per-month breakdown
        months: dict[str, dict] = {}
        for t in all_txns:
            m = _txn_month(t)
            entry = months.setdefault(m, {"month": m, "bank_count": 0, "upi_count": 0})
            entry["bank_count" if t.get("source", "bank") == "bank" else "upi_count"] += 1
        result["months"] = sorted(months.values(), key=lambda x: x["month"], reverse=True)
        return result

    @router.post("/resolve-duplicate")
    async def resolve_duplicate(body: dict, user=Depends(get_current_user)):
        """Deduplicate a verified match by deleting one side. The user chooses which
        (usually the bank side, since UPI has richer metadata)."""
        uid = str(user["_id"])
        keep_id = body.get("keep_id")
        drop_id = body.get("drop_id")
        if not keep_id or not drop_id:
            raise HTTPException(400, "keep_id and drop_id are required.")
        result = await db.finaura_transactions.delete_one({"id": drop_id, "user_id": uid})
        if result.deleted_count == 0:
            raise HTTPException(404, "Transaction not found or not owned by you.")
        # Tag the survivor as verified
        await db.finaura_transactions.update_one(
            {"id": keep_id, "user_id": uid},
            {"$set": {"verified": True, "verified_at": datetime.now(timezone.utc)}},
        )
        return {"deleted": 1, "verified": keep_id}

    return router
