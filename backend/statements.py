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
    "Food": ["swiggy", "zomato", "restaurant", "cafe", "coffee", "starbucks", "domino", "pizza", "food", "eat", "dine"],
    "Rent": ["rent", "landlord", "housing"],
    "Bills": ["electricity", "water", "gas", "internet", "wifi", "airtel", "jio", "vodafone", "bill", "utility"],
    "Transport": ["uber", "ola", "metro", "petrol", "fuel", "taxi", "cab", "rapido", "irctc"],
    "Shopping": ["amazon", "flipkart", "myntra", "ajio", "shopping", "mall", "nykaa"],
    "Entertainment": ["netflix", "spotify", "prime", "hotstar", "cinema", "bookmyshow"],
    "Healthcare": ["hospital", "pharma", "medi", "chemist", "clinic"],
    "Education": ["school", "college", "coursera", "udemy", "tuition", "fee"],
    "Income": ["salary", "payroll", "credit", "interest", "dividend", "refund"],
}


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
    """Return (type, absolute amount). Prefer explicit hint like 'CR'/'DR'."""
    if hint:
        h = hint.strip().lower()
        if h in ("credit", "cr", "c", "in", "income", "+"):
            return "Income", abs(amount)
        if h in ("debit", "dr", "d", "out", "expense", "-"):
            return "Expense", abs(amount)
    if amount > 0:
        return "Income", amount
    return "Expense", abs(amount)


# -------- CSV parsing --------

def csv_preview(content: bytes) -> dict:
    """Return columns + first 5 rows so the client can map fields."""
    text = content.decode("utf-8", errors="ignore")
    df = pd.read_csv(io.StringIO(text), nrows=50, on_bad_lines="skip")
    df.columns = [str(c).strip() for c in df.columns]
    columns = list(df.columns)
    guess = _auto_map_columns(columns)
    rows = df.head(5).fillna("").astype(str).to_dict(orient="records")
    return {"columns": columns, "rows": rows, "guess": guess, "total_rows": len(df)}


def _auto_map_columns(columns: list[str]) -> dict:
    lower = {c.lower(): c for c in columns}
    def find(*names):
        for n in names:
            for k, v in lower.items():
                if n in k:
                    return v
        return None
    return {
        "date": find("date", "txn date", "value date", "posting"),
        "description": find("description", "narration", "particulars", "details", "remarks"),
        "amount": find("amount", "value"),
        "debit": find("debit", "withdrawal", "dr"),
        "credit": find("credit", "deposit", "cr"),
        "type": find("type", "cr/dr", "dr/cr"),
    }


def parse_csv(content: bytes, mapping: dict) -> list[dict]:
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
        elif mapping.get("debit") or mapping.get("credit"):
            dr = normalize_amount(row.get(mapping.get("debit"))) if mapping.get("debit") else 0
            cr = normalize_amount(row.get(mapping.get("credit"))) if mapping.get("credit") else 0
            if cr:
                txn_type = "Income"; amount = abs(cr)
            elif dr:
                txn_type = "Expense"; amount = abs(dr)
        if amount <= 0 or not str(desc_val).strip():
            continue
        transactions.append({
            "id": str(uuid.uuid4()),
            "date": normalize_date(date_val),
            "description": str(desc_val).strip()[:120],
            "amount": round(amount, 2),
            "type": txn_type,
            "category": guess_category(str(desc_val), txn_type),
        })
    return transactions


# -------- Excel parsing --------

def parse_excel(content: bytes, mapping: dict) -> list[dict]:
    df = pd.read_excel(io.BytesIO(content))
    df.columns = [str(c).strip() for c in df.columns]
    csv_bytes = df.to_csv(index=False).encode("utf-8")
    return parse_csv(csv_bytes, mapping)


def excel_preview(content: bytes) -> dict:
    df = pd.read_excel(io.BytesIO(content), nrows=50)
    df.columns = [str(c).strip() for c in df.columns]
    columns = list(df.columns)
    return {
        "columns": columns,
        "rows": df.head(5).fillna("").astype(str).to_dict(orient="records"),
        "guess": _auto_map_columns(columns),
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

def parse_pdf(content: bytes) -> tuple[list[dict], str]:
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
        transactions.append({
            "id": str(uuid.uuid4()),
            "date": normalize_date(date_match.group(1)),
            "description": desc[:120],
            "amount": round(absolute_amount, 2),
            "type": txn_type,
            "category": guess_category(desc, txn_type),
        })
    return transactions, "\n".join(lines[:6])


# -------- FastAPI router --------

ALLOWED_CATEGORIES = {"Income", "Food", "Shopping", "Transport", "Rent", "Bills", "Education", "Entertainment", "Healthcare", "Other"}
ALLOWED_TYPES = {"Income", "Expense"}


class ConfirmImportInput(BaseModel):
    transactions: list[dict]


def build_statements_router(db: AsyncIOMotorDatabase, get_current_user):
    router = APIRouter(prefix="/statements", tags=["statements"])

    @router.post("/preview")
    async def preview(
        file: UploadFile = File(...),
        user=Depends(get_current_user),
    ):
        raw = await file.read()
        if not raw:
            raise HTTPException(status_code=400, detail="Empty file.")
        if len(raw) > 10 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="File is larger than 10 MB.")
        name = (file.filename or "").lower()
        if name.endswith(".csv") or (file.content_type or "").startswith("text/csv"):
            try:
                info = csv_preview(raw)
            except Exception as exc:
                raise HTTPException(status_code=400, detail=f"Couldn't read this CSV: {exc}") from exc
            return {"kind": "csv", **info}
        if name.endswith(".xlsx") or name.endswith(".xls"):
            try:
                info = excel_preview(raw)
            except Exception as exc:
                raise HTTPException(status_code=400, detail=f"Couldn't read this spreadsheet: {exc}") from exc
            return {"kind": "excel", **info}
        if name.endswith(".pdf") or (file.content_type or "") == "application/pdf":
            transactions, sample = parse_pdf(raw)
            return {"kind": "pdf", "transactions": transactions, "sample_lines": sample}
        raise HTTPException(status_code=415, detail="Unsupported file. Please upload CSV, Excel, or PDF.")

    @router.post("/parse")
    async def parse(
        file: UploadFile = File(...),
        mapping: str = Form("{}"),
        user=Depends(get_current_user),
    ):
        """Return transactions extracted with the provided column mapping (CSV/Excel)
        or best-effort text extraction (PDF). Nothing is stored yet."""
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
            return {"transactions": parse_csv(raw, mapping_dict)}
        if name.endswith(".xlsx") or name.endswith(".xls"):
            return {"transactions": parse_excel(raw, mapping_dict)}
        if name.endswith(".pdf"):
            transactions, _ = parse_pdf(raw)
            return {"transactions": transactions}
        raise HTTPException(status_code=415, detail="Unsupported file.")

    @router.post("/confirm-import")
    async def confirm_import(body: ConfirmImportInput, user=Depends(get_current_user)):
        uid = str(user["_id"])
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
            clean.append({
                "id": str(uuid.uuid4()),
                "user_id": uid,
                "date": (t.get("date") or datetime.now(timezone.utc).strftime("%d %b %Y"))[:32],
                "description": desc[:120],
                "amount": round(amount, 2),
                "type": txn_type,
                "category": category,
                "created_at": datetime.now(timezone.utc),
            })
        if clean:
            await db.finaura_transactions.insert_many(clean)
        return {"imported": len(clean)}

    return router
