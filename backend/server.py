"""Finaura backend — main server."""
from dotenv import load_dotenv
from pathlib import Path

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

import os
import uuid
import logging
from datetime import datetime, timezone
from typing import Optional, List

from fastapi import FastAPI, APIRouter, HTTPException, Depends, Request
from fastapi.responses import StreamingResponse
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel

from auth import build_auth_router, make_get_current_user, ensure_indexes, public_user
from passkeys import build_passkey_router, ensure_passkey_indexes
from statements import build_statements_router
from memories import build_memory_router, ensure_memory_indexes, retrieve_relevant
from features import build_learn_router, build_whatif_router

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("finaura")

mongo_url = os.environ["MONGO_URL"]
client = AsyncIOMotorClient(mongo_url, tz_aware=True)
db = client[os.environ["DB_NAME"]]

app = FastAPI(title="Finaura API")
api_router = APIRouter(prefix="/api")

# ============ Static demo data (shared, read-only, no user_id) ============

MONTHS = ["Mar", "Apr", "May", "Jun", "Jul", "Aug"]
HISTORY = [
    {"month": "Mar", "income": 175000, "expenses": 104000, "savings": 71000, "savings_rate": 41},
    {"month": "Apr", "income": 178000, "expenses": 112000, "savings": 66000, "savings_rate": 37},
    {"month": "May", "income": 180000, "expenses": 118000, "savings": 62000, "savings_rate": 34},
    {"month": "Jun", "income": 180000, "expenses": 126000, "savings": 54000, "savings_rate": 30},
    {"month": "Jul", "income": 185000, "expenses": 129000, "savings": 56000, "savings_rate": 30},
    {"month": "Aug", "income": 185000, "expenses": 123000, "savings": 62000, "savings_rate": 34},
]
TRANSACTIONS = [
    {"id": "txn-1", "date": "12 Aug 2026", "description": "SWIGGY", "amount": 450, "type": "Expense", "category": "Food"},
    {"id": "txn-2", "date": "01 Aug 2026", "description": "SALARY", "amount": 185000, "type": "Income", "category": "Income"},
    {"id": "txn-3", "date": "03 Aug 2026", "description": "RENT PAYMENT", "amount": 32000, "type": "Expense", "category": "Rent"},
    {"id": "txn-4", "date": "06 Aug 2026", "description": "AMAZON INDIA", "amount": 3890, "type": "Expense", "category": "Shopping"},
    {"id": "txn-5", "date": "08 Aug 2026", "description": "METRO CARD", "amount": 1250, "type": "Expense", "category": "Transport"},
    {"id": "txn-6", "date": "10 Aug 2026", "description": "NETFLIX", "amount": 649, "type": "Expense", "category": "Entertainment"},
]
DEFAULT_GOALS = [
    {"id": "goal-1", "name": "Higher Education", "emoji": "🎓", "target_amount": 1000000, "current_amount": 300000, "deadline": "2029", "priority": "High", "monthly_contribution": 25000},
    {"id": "goal-2", "name": "Emergency Fund", "emoji": "◉", "target_amount": 300000, "current_amount": 180000, "deadline": "2027", "priority": "High", "monthly_contribution": 15000},
    {"id": "goal-3", "name": "Car", "emoji": "🚗", "target_amount": 800000, "current_amount": 120000, "deadline": "2030", "priority": "Medium", "monthly_contribution": 10000},
]
DEMO_SUMMARY = {
    "income": 185000, "expenses": 123000, "savings": 62000,
    "current_savings": 500000, "investments": 250000, "debt": 120000,
    "emi": 18000, "net_worth": 3485000, "health_score": 78,
}
DEMO_SPENDING = [
    {"name": "Rent", "value": 32000, "color": "#0f172a"},
    {"name": "Food", "value": 18500, "color": "#10b981"},
    {"name": "Shopping", "value": 16000, "color": "#f59e0b"},
    {"name": "Transport", "value": 9200, "color": "#f97316"},
    {"name": "Other", "value": 47300, "color": "#cbd5e1"},
]
DEMO_USER = {"name": "Aarav Sharma", "occupation": "Product Designer", "age": 29}


def _demo_payload() -> dict:
    return {
        "mode": "demo",
        "user": DEMO_USER,
        "summary": DEMO_SUMMARY,
        "history": HISTORY,
        "transactions": TRANSACTIONS,
        "goals": DEFAULT_GOALS,
        "spending": DEMO_SPENDING,
    }


# ============ Schemas ============

class GoalInput(BaseModel):
    name: str
    target_amount: int
    current_amount: int = 0
    deadline: str
    priority: str = "Medium"
    monthly_contribution: int = 0
    emoji: Optional[str] = "✦"
    order: Optional[int] = None


class GoalReorderInput(BaseModel):
    ordered_ids: list[str]


class GoalPatchInput(BaseModel):
    name: Optional[str] = None
    target_amount: Optional[int] = None
    current_amount: Optional[int] = None
    deadline: Optional[str] = None
    priority: Optional[str] = None
    monthly_contribution: Optional[int] = None
    emoji: Optional[str] = None
    order: Optional[int] = None


class CategoryUpdate(BaseModel):
    category: str


class ProfileInput(BaseModel):
    name: Optional[str] = None
    occupation: Optional[str] = None
    age: Optional[int] = None
    phone: Optional[str] = None
    dob: Optional[str] = None
    location: Optional[str] = None
    financial_experience: Optional[str] = None  # "beginner"|"intermediate"|"advanced"
    risk_tolerance: Optional[str] = None  # "conservative"|"balanced"|"aggressive"
    interests: Optional[list[str]] = None  # e.g. ["mutual-funds","tax","sip"]
    avatar_url: Optional[str] = None
    monthly_income: Optional[int] = None
    monthly_expenses: Optional[int] = None
    current_savings: Optional[int] = None
    investments: Optional[int] = None
    debt: Optional[int] = None
    emi: Optional[int] = None


class ChatInput(BaseModel):
    message: str
    model: Optional[str] = "openai"  # "openai" | "claude"


CHAT_MODELS = {
    "openai": {"provider": "openai", "name": "gpt-5.4", "label": "OpenAI GPT-5.4"},
    "claude": {"provider": "anthropic", "name": "claude-sonnet-5", "label": "Claude Sonnet 5"},
}


# ============ Public demo endpoint ============

@api_router.get("/demo/overview")
async def demo_overview():
    return _demo_payload()


# ============ Auth-scoped finance endpoints ============

get_current_user = make_get_current_user(db)


def _clean(doc: dict) -> dict:
    doc.pop("_id", None)
    doc.pop("user_id", None)
    doc.pop("created_at", None)
    return doc


def _empty_summary() -> dict:
    return {
        "income": 0, "expenses": 0, "savings": 0,
        "current_savings": 0, "investments": 0, "debt": 0,
        "emi": 0, "net_worth": 0, "health_score": 0,
    }


def _empty_history() -> list:
    return [{"month": m, "income": 0, "expenses": 0, "savings": 0, "savings_rate": 0} for m in MONTHS]


def _compute_spending(transactions: list) -> list:
    palette = {"Rent": "#0f172a", "Food": "#10b981", "Shopping": "#f59e0b", "Transport": "#f97316",
               "Bills": "#6366f1", "Entertainment": "#ec4899", "Healthcare": "#14b8a6",
               "Education": "#8b5cf6", "Other": "#cbd5e1"}
    totals: dict = {}
    for t in transactions:
        if t.get("type") == "Expense":
            cat = t.get("category", "Other")
            totals[cat] = totals.get(cat, 0) + int(t.get("amount", 0))
    return [{"name": k, "value": v, "color": palette.get(k, "#cbd5e1")} for k, v in totals.items()]


_PRIORITY_RANK = {"High": 0, "Medium": 1, "Low": 2}


async def _load_ordered_goals(uid: str) -> list:
    """Return goals sorted by (order asc, priority High->Low, created_at asc)."""
    raw = [_clean(g) for g in await db.finaura_goals.find({"user_id": uid}).to_list(200)]
    def _key(g):
        return (
            g.get("order") if g.get("order") is not None else 10_000,
            _PRIORITY_RANK.get(g.get("priority", "Medium"), 1),
            str(g.get("created_at", "")),
        )
    return sorted(raw, key=_key)


@api_router.get("/financial/overview")
async def overview(user=Depends(get_current_user)):
    uid = str(user["_id"])
    goals = await _load_ordered_goals(uid)
    txns_cursor = db.finaura_transactions.find({"user_id": uid})
    transactions = [_clean(t) for t in await txns_cursor.to_list(200)]
    # Cross-source dedupe: if same amount+date+type appears in both bank & UPI, count once.
    # Prefer keeping the UPI entry (richer merchant/category), drop bank duplicate from analytics.
    seen_keys: set = set()
    deduped: list = []
    upi_signatures = {
        (t.get("amount"), t.get("date"), t.get("type"))
        for t in transactions if t.get("source") == "upi"
    }
    for t in transactions:
        sig = (t.get("amount"), t.get("date"), t.get("type"))
        if t.get("source", "bank") == "bank" and sig in upi_signatures:
            # duplicate of a UPI txn — skip in analytics (mark visually only)
            continue
        if sig in seen_keys and t.get("source") == "bank":
            continue
        seen_keys.add(sig)
        deduped.append(t)
    transactions = deduped
    profile = user.get("profile") or {}
    if user.get("has_demo_data") and profile == {}:
        # Use demo summary snapshot for demo-imported users so metrics show
        summary = dict(DEMO_SUMMARY)
        history = HISTORY
    else:
        income = int(profile.get("monthly_income", 0))
        expenses = int(profile.get("monthly_expenses", 0))
        summary = {
            "income": income,
            "expenses": expenses,
            "savings": max(0, income - expenses),
            "current_savings": int(profile.get("current_savings", 0)),
            "investments": int(profile.get("investments", 0)),
            "debt": int(profile.get("debt", 0)),
            "emi": int(profile.get("emi", 0)),
            "net_worth": int(profile.get("current_savings", 0)) + int(profile.get("investments", 0)) - int(profile.get("debt", 0)),
            "health_score": _health_score(profile, goals),
        }
        history = _empty_history()
    spending = _compute_spending(transactions) if transactions else (DEMO_SPENDING if user.get("has_demo_data") else [])
    return {
        "mode": "user",
        "user": {
            "name": user.get("name") or (user.get("email") or "").split("@")[0].title(),
            "occupation": profile.get("occupation") or "",
            "age": profile.get("age"),
            "email": user.get("email"),
        },
        "summary": summary,
        "history": history,
        "transactions": transactions,
        "goals": goals,
        "spending": spending,
        "has_demo_data": bool(user.get("has_demo_data")),
    }


def _health_score(profile: dict, goals: list) -> int:
    income = int(profile.get("monthly_income", 0) or 0)
    expenses = int(profile.get("monthly_expenses", 0) or 0)
    savings_rate = ((income - expenses) / income * 100) if income else 0
    debt = int(profile.get("debt", 0) or 0)
    debt_ratio = (debt / income) if income else 0
    goal_progress = 0
    if goals:
        goal_progress = sum(min(1, (g.get("current_amount", 0) / g["target_amount"])) for g in goals if g.get("target_amount")) / len(goals) * 100
    score = 40 + min(30, max(0, savings_rate) * 0.6) + max(0, 20 - debt_ratio * 5) + goal_progress * 0.1
    return max(0, min(100, round(score)))


@api_router.get("/user/profile")
async def get_profile(user=Depends(get_current_user)):
    profile = user.get("profile") or {}
    return {
        "name": user.get("name"),
        "email": user.get("email"),
        "email_verified": bool(user.get("email_verified")),
        **{k: profile.get(k) for k in ["occupation","age","phone","dob","location","financial_experience","risk_tolerance","interests","avatar_url","monthly_income","monthly_expenses","current_savings","investments","debt","emi"]},
    }


@api_router.patch("/user/profile")
async def update_profile(body: ProfileInput, user=Depends(get_current_user)):
    update = {}
    if body.name is not None:
        update["name"] = body.name.strip()[:80]
    profile = dict(user.get("profile") or {})
    for field in ["occupation","age","phone","dob","location","financial_experience","risk_tolerance","interests","avatar_url","monthly_income","monthly_expenses","current_savings","investments","debt","emi"]:
        value = getattr(body, field)
        if value is not None:
            profile[field] = value
    update["profile"] = profile
    update["updated_at"] = datetime.now(timezone.utc)
    await db.users.update_one({"_id": user["_id"]}, {"$set": update})
    return {"ok": True}


# ============ Settings preferences ============

DEFAULT_PREFERENCES = {
    "currency": "INR",
    "date_format": "DD-MM-YYYY",
    "theme": "system",  # light | dark | system
    "language": "en",
    "goal_default_priority": "Medium",
    "goal_default_deadline_years": 5,
    "budget_alert_threshold_pct": 80,
    "notifications": {
        "goal_reminders": True,
        "budget_alerts": True,
        "payment_reminders": True,
        "financial_insights": True,
        "ai_recommendations": True,
        "weekly_digest": False,
    },
    "reports": {
        "monthly_summary": True,
    },
}


def _merge_prefs(saved: dict) -> dict:
    """Deep-merge saved prefs onto defaults so newly added keys always exist."""
    out = {k: (v.copy() if isinstance(v, dict) else v) for k, v in DEFAULT_PREFERENCES.items()}
    saved = saved or {}
    for k, v in saved.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = {**out[k], **v}
        else:
            out[k] = v
    return out


class PreferencesInput(BaseModel):
    currency: Optional[str] = None
    date_format: Optional[str] = None
    theme: Optional[str] = None
    language: Optional[str] = None
    goal_default_priority: Optional[str] = None
    goal_default_deadline_years: Optional[int] = None
    budget_alert_threshold_pct: Optional[int] = None
    notifications: Optional[dict] = None
    reports: Optional[dict] = None


@api_router.get("/settings/preferences")
async def get_preferences(user=Depends(get_current_user)):
    return _merge_prefs(user.get("preferences") or {})


@api_router.patch("/settings/preferences")
async def update_preferences(body: PreferencesInput, user=Depends(get_current_user)):
    current = _merge_prefs(user.get("preferences") or {})
    updates = body.model_dump(exclude_none=True)
    # Deep-merge nested dicts
    for k, v in updates.items():
        if isinstance(v, dict) and isinstance(current.get(k), dict):
            current[k] = {**current[k], **v}
        else:
            current[k] = v
    # Enum validation (soft — bad values fall back to default)
    if current.get("theme") not in {"light", "dark", "system"}:
        current["theme"] = "system"
    if current.get("goal_default_priority") not in {"High", "Medium", "Low"}:
        current["goal_default_priority"] = "Medium"
    await db.users.update_one(
        {"_id": user["_id"]},
        {"$set": {"preferences": current, "updated_at": datetime.now(timezone.utc)}},
    )
    return current


@api_router.get("/settings/export")
async def export_data(user=Depends(get_current_user)):
    """Return the user's full financial data for download (JSON)."""
    uid = str(user["_id"])
    goals = [_clean(g) for g in await db.finaura_goals.find({"user_id": uid}).to_list(500)]
    txns = [_clean(t) for t in await db.finaura_transactions.find({"user_id": uid}).to_list(5000)]
    memories = [_clean(m) for m in await db.finaura_memories.find({"user_id": uid}).to_list(500)]
    return {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "user": {
            "name": user.get("name"),
            "email": user.get("email"),
            "profile": user.get("profile") or {},
            "preferences": _merge_prefs(user.get("preferences") or {}),
        },
        "goals": goals,
        "transactions": txns,
        "memories": memories,
    }


@api_router.post("/goals")
async def create_goal(goal: GoalInput, user=Depends(get_current_user)):
    uid = str(user["_id"])
    doc = goal.model_dump()
    if doc.get("order") is None:
        # place new goal at the end
        count = await db.finaura_goals.count_documents({"user_id": uid})
        doc["order"] = count
    doc["id"] = str(uuid.uuid4())
    doc["user_id"] = uid
    doc["created_at"] = datetime.now(timezone.utc)
    await db.finaura_goals.insert_one(doc)
    return _clean(dict(doc))


@api_router.patch("/goals/{goal_id}")
async def update_goal(goal_id: str, goal: GoalPatchInput, user=Depends(get_current_user)):
    doc = goal.model_dump(exclude_none=True)
    if not doc:
        raise HTTPException(status_code=400, detail="No fields to update.")
    result = await db.finaura_goals.update_one(
        {"id": goal_id, "user_id": str(user["_id"])},
        {"$set": doc},
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Goal not found")
    return {"id": goal_id, **doc}


@api_router.post("/goals/reorder")
async def reorder_goals(body: GoalReorderInput, user=Depends(get_current_user)):
    """Persist the exact goal order the user has chosen (drag/drop or arrows).
    Any goal not in ordered_ids is left where it was after the reordered ones."""
    uid = str(user["_id"])
    # Verify all ids belong to this user (server-side authz)
    owned = {g["id"] async for g in db.finaura_goals.find({"user_id": uid}, {"id": 1})}
    unknown = [gid for gid in body.ordered_ids if gid not in owned]
    if unknown:
        raise HTTPException(status_code=404, detail="Some goals do not belong to you or don't exist.")
    from pymongo import UpdateOne
    ops = [
        UpdateOne({"id": gid, "user_id": uid}, {"$set": {"order": idx}})
        for idx, gid in enumerate(body.ordered_ids)
    ]
    if ops:
        await db.finaura_goals.bulk_write(ops)
    return {"reordered": len(ops)}


@api_router.delete("/goals/{goal_id}")
async def delete_goal(goal_id: str, user=Depends(get_current_user)):
    result = await db.finaura_goals.delete_one({"id": goal_id, "user_id": str(user["_id"])})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Goal not found")
    return {"deleted": True}


@api_router.patch("/transactions/{txn_id}")
async def update_transaction(txn_id: str, update: CategoryUpdate, user=Depends(get_current_user)):
    result = await db.finaura_transactions.update_one(
        {"id": txn_id, "user_id": str(user["_id"])},
        {"$set": {"category": update.category}},
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return {"id": txn_id, "category": update.category}


@api_router.post("/statements/import-demo")
async def import_demo_statement(user=Depends(get_current_user)):
    """Simulated import — populates the user's account with the six-month demo dataset."""
    uid = str(user["_id"])
    existing = await db.finaura_transactions.count_documents({"user_id": uid})
    if existing == 0:
        await db.finaura_transactions.insert_many([
            {**t, "id": str(uuid.uuid4()), "user_id": uid, "created_at": datetime.now(timezone.utc)} for t in TRANSACTIONS
        ])
    await db.users.update_one(
        {"_id": user["_id"]},
        {"$set": {"has_demo_data": True, "updated_at": datetime.now(timezone.utc)}},
    )
    return {"imported": True, "count": len(TRANSACTIONS)}


@api_router.delete("/financial/data")
async def delete_data(user=Depends(get_current_user)):
    uid = str(user["_id"])
    await db.finaura_goals.delete_many({"user_id": uid})
    await db.finaura_transactions.delete_many({"user_id": uid})
    await db.users.update_one({"_id": user["_id"]}, {"$set": {"has_demo_data": False}})
    return {"deleted": True}


# ============ Ask Finaura (chat) ============

INDIAN_TAX_CONTEXT_2025_26 = (
    "FINAURA AI Indian financial context (educational, verify latest on official sites):\n"
    "Current financial year: FY 2025-26 (AY 2026-27).\n"
    "NEW regime slabs (FY 2025-26): 0% up to ₹4L · 5% ₹4-8L · 10% ₹8-12L · 15% ₹12-16L · "
    "20% ₹16-20L · 25% ₹20-24L · 30% above ₹24L. Standard deduction ₹75,000 for salaried. "
    "Section 87A rebate makes income up to ₹12L effectively tax-free for many salaried employees.\n"
    "OLD regime slabs: 0% up to ₹2.5L · 5% up to ₹5L · 20% up to ₹10L · 30% above ₹10L. "
    "80C limit ₹1.5L; 80D health cover ₹25k self + ₹25/50k parents; 80CCD(1B) NPS extra ₹50k.\n"
    "Capital gains (equity, FY 2025-26): STCG (<12m) 20%; LTCG (>12m) 12.5% above ₹1.25L/yr.\n"
    "Debt fund gains from 1 Apr 2023 are taxed at your slab rate (no indexation).\n"
    "Regulators — RBI: banking + monetary policy · SEBI: capital markets + mutual funds · "
    "IRDAI: insurance · PFRDA: NPS · IT Dept: income tax filing.\n"
    "Always mention that time-sensitive rates should be verified on the official Income Tax "
    "Department / RBI / SEBI portal and that this is educational content, not personal advice."
)


async def _system_prompt(user: Optional[dict], user_message: str = "") -> str:
    base_rules = (
        "You are FINAURA AI, a warm, concise financial education assistant for Indian users. "
        "Rules: (1) Never invent tax rates, sections, laws, deadlines, government schemes or "
        "specific numbers. If unsure, say so. (2) Always mention 'educational, not personal advice' "
        "for tax/investment questions. (3) Prefer INR (₹) formatting. (4) Use short paragraphs, "
        "bullets or small tables when helpful. (5) Never claim bank access or exact market prices. "
        "(6) Use previously stored user information when relevant — don't ask for facts already on file. "
        "(7) If the user's stored data is missing something needed, ask a focused follow-up."
    )
    if user is None:
        return (
            f"{base_rules}\n\n{INDIAN_TAX_CONTEXT_2025_26}\n\n"
            "This user is exploring a public demo profile: Aarav Sharma, monthly income ₹1,85,000, "
            "expenses ₹1,23,000, savings ₹62,000, net worth ₹34,85,000, health score 78. Goals: "
            "Higher Education (high, ₹10L by 2029), Emergency Fund (high, ₹3L by 2027), Car (medium, "
            "₹8L by 2030). Mention that this is demo data when it comes up."
        )
    uid = str(user["_id"])
    profile = user.get("profile") or {}
    goals = [_clean(g) for g in await db.finaura_goals.find({"user_id": uid}).to_list(20)]
    txn_count = await db.finaura_transactions.count_documents({"user_id": uid})
    goals_summary = "; ".join([
        f"{g['name']} priority={g.get('priority','Medium')} target=₹{g.get('target_amount',0):,} "
        f"saved=₹{g.get('current_amount',0):,} deadline={g.get('deadline','')}"
        for g in goals
    ]) or "no goals stored yet"

    if user.get("has_demo_data") and profile == {}:
        s = DEMO_SUMMARY
        financial_context = (
            f"monthly_income ₹{s['income']:,}, monthly_expenses ₹{s['expenses']:,}, "
            f"current_savings ₹{s['current_savings']:,}, investments ₹{s['investments']:,}, "
            f"debt ₹{s['debt']:,}, net_worth ₹{s['net_worth']:,} (these are the demo dataset numbers "
            f"shown on the user's dashboard)."
        )
    else:
        financial_context = (
            f"monthly_income ₹{profile.get('monthly_income', 'not on file')}, "
            f"monthly_expenses ₹{profile.get('monthly_expenses', 'not on file')}, "
            f"current_savings ₹{profile.get('current_savings', 'not on file')}, "
            f"investments ₹{profile.get('investments', 'not on file')}, "
            f"debt ₹{profile.get('debt', 'not on file')}, "
            f"emi ₹{profile.get('emi', 'not on file')}."
        )
    persona = (
        f"occupation={profile.get('occupation') or 'not on file'}, "
        f"age={profile.get('age') or 'not on file'}, "
        f"risk_tolerance={profile.get('risk_tolerance') or 'not on file'}, "
        f"experience={profile.get('financial_experience') or 'not on file'}, "
        f"interests={', '.join(profile.get('interests') or []) or 'not on file'}."
    )
    memories = await retrieve_relevant(db, uid, user_message, limit=8)
    memory_block = ""
    if memories:
        lines = "\n".join([
            f"- [{m['category']}] {m['key']}: {m['value']}"
            + (f" ({m['numeric_value']} {m['unit'] or ''})" if m.get('numeric_value') is not None else "")
            + f" · updated {m.get('updated_at','')[:10]}"
            for m in memories
        ])
        memory_block = f"\nStored user memories (long-term, use when relevant):\n{lines}\n"
    return (
        f"{base_rules}\n\n{INDIAN_TAX_CONTEXT_2025_26}\n\n"
        f"You are speaking with {user.get('name') or 'a Finaura user'}. Persona: {persona} "
        f"Current finances: {financial_context} Goals: {goals_summary}. "
        f"They have {txn_count} recorded transactions.{memory_block}"
    )


@api_router.post("/chat")
async def chat(payload: ChatInput, request: Request):
    key = os.environ.get("EMERGENT_LLM_KEY")
    if not key:
        raise HTTPException(503, "AI assistant is not configured")
    # Optional auth: try to get the user, but allow anonymous demo mode
    user_doc = None
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        try:
            user_doc = await get_current_user(request)
        except HTTPException:
            user_doc = None
    system = await _system_prompt(user_doc, payload.message)
    model_cfg = CHAT_MODELS.get(payload.model or "openai", CHAT_MODELS["openai"])
    session_id = f"finaura-{str(user_doc['_id']) if user_doc else 'demo'}-{model_cfg['provider']}"

    from emergentintegrations.llm.chat import LlmChat, UserMessage, TextDelta, StreamDone

    async def stream():
        chat_client = LlmChat(api_key=key, session_id=session_id, system_message=system).with_model(
            model_cfg["provider"], model_cfg["name"]
        )
        async for event in chat_client.stream_message(UserMessage(text=payload.message)):
            if isinstance(event, TextDelta):
                yield event.content
            elif isinstance(event, StreamDone):
                break

    return StreamingResponse(
        stream(),
        media_type="text/plain",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "X-Model": model_cfg["label"]},
    )


@api_router.get("/chat/models")
async def chat_models():
    return {
        "models": [
            {"id": key, "label": cfg["label"], "provider": cfg["provider"]}
            for key, cfg in CHAT_MODELS.items()
        ],
        "default": "openai",
    }


# ============ Config info endpoint (frontend uses this to know which OAuth to show) ============

@api_router.get("/auth/config")
async def auth_config():
    google_id = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
    google_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
    google_redirect = os.environ.get("GOOGLE_REDIRECT_URI", "").strip()
    return {
        "google_enabled": bool(google_id),
        "google_client_id": google_id,
        # Server-side auth-code flow available only when secret + redirect are configured
        "google_authcode_enabled": bool(google_id and google_secret and google_redirect),
        "apple_enabled": bool(os.environ.get("APPLE_CLIENT_ID", "").strip()),
        "apple_client_id": os.environ.get("APPLE_CLIENT_ID", "").strip(),
        "apple_redirect_uri": os.environ.get("APPLE_REDIRECT_URI", "").strip(),
        "resend_enabled": bool(os.environ.get("RESEND_API_KEY", "").strip()),
    }


# ============ Wire routers & CORS ============

app.include_router(build_auth_router(db), prefix="/api")
app.include_router(build_passkey_router(db, get_current_user), prefix="/api")
app.include_router(build_statements_router(db, get_current_user), prefix="/api")
app.include_router(build_memory_router(db, get_current_user), prefix="/api")
app.include_router(build_learn_router(db, get_current_user), prefix="/api")
app.include_router(build_whatif_router(get_current_user, db), prefix="/api")
app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def _startup():
    try:
        await ensure_indexes(db)
        await ensure_passkey_indexes(db)
        await ensure_memory_indexes(db)
    except Exception:
        log.exception("Failed to create indexes")


@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
