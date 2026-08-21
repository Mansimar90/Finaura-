"""Long-term structured memory for FINAURA AI.

Structured facts (name, monthly_income, goal_deadline etc.) stored per user with
timestamps + soft "confirmed_at" flag, retrieved by category or free-text keyword
match into the chat system prompt.
"""
from __future__ import annotations

import uuid
import re
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from motor.motor_asyncio import AsyncIOMotorDatabase

MEMORY_CATEGORIES = {
    "profile", "income", "expense", "goal", "preference",
    "risk", "investment", "tax", "debt", "insurance", "other",
}

INTENT_KEYWORDS = {
    "income": ["income", "salary", "earn", "monthly income", "annual income"],
    "expense": ["expense", "spend", "spending", "cost", "outgoing", "bills"],
    "goal": ["goal", "target", "objective", "plan", "save for"],
    "risk": ["risk", "tolerance", "aggressive", "conservative"],
    "investment": ["invest", "sip", "mutual fund", "stock", "portfolio", "returns"],
    "tax": ["tax", "80c", "80d", "hra", "regime", "deduction"],
    "debt": ["loan", "emi", "debt", "credit card"],
    "insurance": ["insurance", "term plan", "health cover", "life cover"],
    "profile": ["age", "occupation", "dependents", "family", "location", "city"],
}


class MemoryInput(BaseModel):
    category: str = Field(min_length=1, max_length=32)
    key: str = Field(min_length=1, max_length=80)
    value: str = Field(min_length=1, max_length=500)
    numeric_value: Optional[float] = None
    unit: Optional[str] = Field(default=None, max_length=16)


class MemoryUpdate(BaseModel):
    value: Optional[str] = Field(default=None, max_length=500)
    numeric_value: Optional[float] = None
    unit: Optional[str] = Field(default=None, max_length=16)


def _clean(doc: dict) -> dict:
    return {
        "id": doc["id"],
        "category": doc["category"],
        "key": doc["key"],
        "value": doc["value"],
        "numeric_value": doc.get("numeric_value"),
        "unit": doc.get("unit"),
        "created_at": doc["created_at"].isoformat() if isinstance(doc.get("created_at"), datetime) else doc.get("created_at"),
        "updated_at": doc["updated_at"].isoformat() if isinstance(doc.get("updated_at"), datetime) else doc.get("updated_at"),
    }


async def retrieve_relevant(db: AsyncIOMotorDatabase, user_id: str, query: str, limit: int = 12) -> list[dict]:
    """Return up to `limit` memories relevant to the query (naive keyword scoring)."""
    all_memories = await db.finaura_memories.find({"user_id": user_id}).sort("updated_at", -1).to_list(200)
    if not all_memories:
        return []
    query_lower = (query or "").lower()
    # Score by keyword match
    scored: list[tuple[int, dict]] = []
    for m in all_memories:
        score = 0
        cat = m.get("category", "")
        if cat in query_lower:
            score += 4
        # Category-implied keywords
        for kw in INTENT_KEYWORDS.get(cat, []):
            if kw in query_lower:
                score += 3
                break
        # Token match on key/value
        tokens = re.findall(r"[a-z0-9]+", query_lower)
        haystack = f"{m.get('key','')} {m.get('value','')}".lower()
        score += sum(1 for t in tokens if t and len(t) > 2 and t in haystack)
        # Bias toward recent
        if m.get("updated_at"):
            days = (datetime.now(timezone.utc) - m["updated_at"].replace(tzinfo=timezone.utc) if m["updated_at"].tzinfo is None else datetime.now(timezone.utc) - m["updated_at"]).days
            if days < 30:
                score += 1
        if score > 0:
            scored.append((score, m))
    scored.sort(key=lambda x: x[0], reverse=True)
    top = [m for _, m in scored[:limit]]
    # Also include the last 5 most recent regardless (baseline profile facts)
    for m in all_memories[:5]:
        if m not in top and len(top) < limit + 5:
            top.append(m)
    return [_clean(m) for m in top]


def build_memory_router(db: AsyncIOMotorDatabase, get_current_user):
    router = APIRouter(prefix="/memories", tags=["memories"])

    @router.get("")
    async def list_memories(category: Optional[str] = None, user=Depends(get_current_user)):
        q: dict = {"user_id": str(user["_id"])}
        if category:
            q["category"] = category
        rows = await db.finaura_memories.find(q).sort("updated_at", -1).to_list(500)
        return {"memories": [_clean(r) for r in rows]}

    @router.post("")
    async def add_memory(body: MemoryInput, user=Depends(get_current_user)):
        cat = body.category.lower().strip()
        if cat not in MEMORY_CATEGORIES:
            raise HTTPException(status_code=400, detail=f"Unknown category '{body.category}'.")
        now = datetime.now(timezone.utc)
        doc = {
            "id": str(uuid.uuid4()),
            "user_id": str(user["_id"]),
            "category": cat,
            "key": body.key.strip(),
            "value": body.value.strip(),
            "numeric_value": body.numeric_value,
            "unit": (body.unit or "").strip() or None,
            "created_at": now,
            "updated_at": now,
        }
        # Upsert by (user, category, key) so re-adding an income updates it
        await db.finaura_memories.update_one(
            {"user_id": doc["user_id"], "category": cat, "key": doc["key"]},
            {"$set": {**doc, "created_at": now}, "$setOnInsert": {"_created_first": now}},
            upsert=True,
        )
        return _clean(doc)

    @router.patch("/{memory_id}")
    async def update_memory(memory_id: str, body: MemoryUpdate, user=Depends(get_current_user)):
        patch: dict = {"updated_at": datetime.now(timezone.utc)}
        if body.value is not None: patch["value"] = body.value.strip()
        if body.numeric_value is not None: patch["numeric_value"] = body.numeric_value
        if body.unit is not None: patch["unit"] = (body.unit or "").strip() or None
        result = await db.finaura_memories.find_one_and_update(
            {"id": memory_id, "user_id": str(user["_id"])},
            {"$set": patch},
            return_document=True,
        )
        if not result:
            raise HTTPException(status_code=404, detail="Memory not found.")
        return _clean(result)

    @router.delete("/{memory_id}")
    async def delete_memory(memory_id: str, user=Depends(get_current_user)):
        r = await db.finaura_memories.delete_one({"id": memory_id, "user_id": str(user["_id"])})
        if r.deleted_count == 0:
            raise HTTPException(status_code=404, detail="Memory not found.")
        return {"deleted": True}

    @router.delete("")
    async def clear_all(user=Depends(get_current_user)):
        r = await db.finaura_memories.delete_many({"user_id": str(user["_id"])})
        return {"deleted": r.deleted_count}

    return router


async def ensure_memory_indexes(db: AsyncIOMotorDatabase) -> None:
    await db.finaura_memories.create_index([("user_id", 1), ("category", 1), ("key", 1)], unique=True)
    await db.finaura_memories.create_index([("user_id", 1), ("updated_at", -1)])
