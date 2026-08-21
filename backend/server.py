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


class CategoryUpdate(BaseModel):
    category: str


class ProfileInput(BaseModel):
    name: Optional[str] = None
    occupation: Optional[str] = None
    age: Optional[int] = None
    monthly_income: Optional[int] = None
    monthly_expenses: Optional[int] = None
    current_savings: Optional[int] = None
    investments: Optional[int] = None
    debt: Optional[int] = None
    emi: Optional[int] = None


class ChatInput(BaseModel):
    message: str


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


@api_router.get("/financial/overview")
async def overview(user=Depends(get_current_user)):
    uid = str(user["_id"])
    goals_cursor = db.finaura_goals.find({"user_id": uid})
    goals = [_clean(g) for g in await goals_cursor.to_list(50)]
    txns_cursor = db.finaura_transactions.find({"user_id": uid})
    transactions = [_clean(t) for t in await txns_cursor.to_list(200)]
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


@api_router.patch("/user/profile")
async def update_profile(body: ProfileInput, user=Depends(get_current_user)):
    update = {}
    if body.name is not None:
        update["name"] = body.name.strip()[:80]
    profile = dict(user.get("profile") or {})
    for field in ["occupation", "age", "monthly_income", "monthly_expenses", "current_savings", "investments", "debt", "emi"]:
        value = getattr(body, field)
        if value is not None:
            profile[field] = value
    update["profile"] = profile
    update["updated_at"] = datetime.now(timezone.utc)
    await db.users.update_one({"_id": user["_id"]}, {"$set": update})
    return {"ok": True}


@api_router.post("/goals")
async def create_goal(goal: GoalInput, user=Depends(get_current_user)):
    doc = goal.model_dump()
    doc["id"] = str(uuid.uuid4())
    doc["user_id"] = str(user["_id"])
    doc["created_at"] = datetime.now(timezone.utc)
    await db.finaura_goals.insert_one(doc)
    return _clean(dict(doc))


@api_router.patch("/goals/{goal_id}")
async def update_goal(goal_id: str, goal: GoalInput, user=Depends(get_current_user)):
    doc = goal.model_dump()
    result = await db.finaura_goals.update_one(
        {"id": goal_id, "user_id": str(user["_id"])},
        {"$set": doc},
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Goal not found")
    return {"id": goal_id, **doc}


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

async def _system_prompt(user: Optional[dict]) -> str:
    if user is None:
        return (
            "You are Ask Finaura, a warm, concise financial education assistant. "
            "This user is exploring a public demo profile: Aarav Sharma, monthly income ₹185,000, "
            "expenses ₹123,000, savings ₹62,000, health score 78, goals Higher Education (high), "
            "Emergency Fund (high), Car (medium). Explain trends and concepts. Never give personalized "
            "investment orders or claim bank access. Mention this is demo data when relevant."
        )
    uid = str(user["_id"])
    profile = user.get("profile") or {}
    goals = [_clean(g) for g in await db.finaura_goals.find({"user_id": uid}).to_list(20)]
    txns = [_clean(t) for t in await db.finaura_transactions.find({"user_id": uid}).to_list(50)]
    goals_summary = ", ".join([f"{g['name']} (₹{g.get('current_amount',0)}/₹{g.get('target_amount',0)}, {g.get('priority','Medium')})" for g in goals]) or "no goals yet"
    financial_context = (
        f"monthly_income ₹{profile.get('monthly_income', 'unknown')}, "
        f"monthly_expenses ₹{profile.get('monthly_expenses', 'unknown')}, "
        f"current_savings ₹{profile.get('current_savings', 'unknown')}, "
        f"debt ₹{profile.get('debt', 'unknown')}."
    )
    return (
        f"You are Ask Finaura, a warm, concise financial education assistant. "
        f"You are speaking with {user.get('name') or 'a Finaura user'}. Their profile: {financial_context} "
        f"Goals: {goals_summary}. They have {len(txns)} recorded transactions. "
        f"Give educational answers grounded in their data. Never give personalized investment orders "
        f"or claim bank access. If profile fields are missing, invite them to update in Settings."
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
    system = await _system_prompt(user_doc)
    session_id = f"finaura-{str(user_doc['_id']) if user_doc else 'demo'}"

    from emergentintegrations.llm.chat import LlmChat, UserMessage, TextDelta

    async def stream():
        chat_client = LlmChat(api_key=key, session_id=session_id, system_message=system).with_model("openai", "gpt-5.4")
        async for event in chat_client.stream_message(UserMessage(text=payload.message)):
            if isinstance(event, TextDelta):
                yield event.content

    return StreamingResponse(stream(), media_type="text/plain", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ============ Config info endpoint (frontend uses this to know which OAuth to show) ============

@api_router.get("/auth/config")
async def auth_config():
    return {
        "google_enabled": bool(os.environ.get("GOOGLE_CLIENT_ID", "").strip()),
        "google_client_id": os.environ.get("GOOGLE_CLIENT_ID", "").strip(),
        "apple_enabled": bool(os.environ.get("APPLE_CLIENT_ID", "").strip()),
        "apple_client_id": os.environ.get("APPLE_CLIENT_ID", "").strip(),
        "apple_redirect_uri": os.environ.get("APPLE_REDIRECT_URI", "").strip(),
        "resend_enabled": bool(os.environ.get("RESEND_API_KEY", "").strip()),
    }


# ============ Wire routers & CORS ============

app.include_router(build_auth_router(db), prefix="/api")
app.include_router(build_passkey_router(db, get_current_user), prefix="/api")
app.include_router(build_statements_router(db, get_current_user), prefix="/api")
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
    except Exception:
        log.exception("Failed to create indexes")


@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
