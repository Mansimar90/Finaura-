"""What-If financial simulator + Learn content + daily learn rotation."""
from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import date, datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from motor.motor_asyncio import AsyncIOMotorDatabase

log = logging.getLogger("finaura.features")

# --------- LEARN CONTENT ----------

LEARN_ARTICLES = [
    {
        "id": "emergency-funds",
        "category": "Personal finance",
        "title": "Build your emergency fund first",
        "read_minutes": 6,
        "tags": ["saving", "safety", "basics"],
        "art_variant": "mint",
        "why_relevant": "Because your current cash reserve may be below 3 months of expenses.",
        "body": [
            {"heading": "Why an emergency fund matters", "text": "An emergency fund is the layer of savings that protects every other financial goal you have. Job loss, medical bills, a broken laptop, an urgent trip home — these things happen, and without a buffer they force you to sell investments, break FDs or take high-interest loans."},
            {"heading": "How much is enough?", "text": "A common Indian rule of thumb: keep 3 to 6 months of essential monthly expenses (rent, EMIs, groceries, bills, insurance premiums) in easily accessible form. Freelancers or single earners with dependents typically aim for 6-12 months."},
            {"heading": "Where to keep it", "text": "Split it across a savings account (1 month) + liquid mutual fund or sweep-in FD (rest). Avoid putting it in equity, ULIPs, or long-duration debt funds — you need liquidity, not returns."},
            {"heading": "Building it up", "text": "If you don't have one yet, aim to save at least 20% of every incoming rupee into this bucket until you hit your target. Automate it via a monthly SIP or standing instruction so it happens before spending."},
        ],
    },
    {
        "id": "mutual-funds-101",
        "category": "Investing basics",
        "title": "Mutual Funds 101 for Indian investors",
        "read_minutes": 8,
        "tags": ["investing", "mutual-funds", "sip"],
        "art_variant": "dark",
        "why_relevant": "You have long-term goals but few investments recorded yet.",
        "body": [
            {"heading": "What is a mutual fund?", "text": "A mutual fund pools money from many investors to buy a diversified basket of stocks, bonds, gold or a mix. You don't buy the underlying — you buy units of the fund, whose NAV (net asset value) moves with the basket."},
            {"heading": "SIP vs Lumpsum", "text": "A Systematic Investment Plan (SIP) invests a fixed amount every month (usually ₹500 upwards), buying more units when NAV is low and fewer when it's high — that's rupee-cost averaging. Lumpsum invests a single big amount and works best when you have a windfall AND markets look reasonable."},
            {"heading": "Equity, Debt or Hybrid?", "text": "Equity funds invest primarily in stocks — higher volatility, better long-term returns. Debt funds hold bonds and are steadier. Hybrid funds mix both. Match the fund category to your goal horizon: <3 years lean debt, 5+ years lean equity."},
            {"heading": "Expense ratio & exit load", "text": "Every mutual fund charges an annual expense ratio (0.2%-2.5%). Direct plans have lower expenses than Regular plans because there's no distributor commission. Exit loads (usually 1% if redeemed within 1 year) discourage short-term trading."},
            {"heading": "Taxation in India (FY 2025-26)", "text": "Equity funds: STCG (<12m) is 20%; LTCG (>12m) is 12.5% above ₹1.25 lakh gains/year. Debt funds: gains are taxed at your slab rate irrespective of holding period. Verify with the Income Tax Department for the current assessment year."},
        ],
    },
    {
        "id": "compound-interest",
        "category": "Long-term planning",
        "title": "The quiet power of compounding",
        "read_minutes": 5,
        "tags": ["compounding", "long-term"],
        "art_variant": "yellow",
        "why_relevant": "A small monthly habit can become meaningful over time.",
        "body": [
            {"heading": "Interest on interest", "text": "Compounding means your returns earn returns. ₹10,000 invested at 12% grows to ₹31,058 in 10 years — but ₹96,463 in 25 years. The extra 15 years do 3× the work of the first 10."},
            {"heading": "Time is the biggest lever", "text": "Two friends save ₹5,000/month. Riya starts at 25 and stops at 35, then never adds. Amit starts at 35 and continues to 60. Riya still ends up richer, because her money had 10 extra years to compound."},
            {"heading": "The 72 rule", "text": "Divide 72 by the annual return to estimate doubling time. At 12% your money doubles roughly every 6 years. At 8% every 9 years."},
        ],
    },
    {
        "id": "inflation-basics",
        "category": "Personal finance",
        "title": "Making sense of inflation",
        "read_minutes": 7,
        "tags": ["inflation", "planning"],
        "art_variant": "peach",
        "why_relevant": "Understand why your long-term goals need a little headroom.",
        "body": [
            {"heading": "What inflation actually is", "text": "Inflation is the rise in the general price level of goods and services. In India, the CPI (consumer price index) has averaged ~5-6% over the past decade. ₹100 today buys less next year."},
            {"heading": "Real vs nominal return", "text": "If your FD gives 7% but inflation is 6%, your real return is 1%. That's the number that matters for buying power."},
            {"heading": "Planning long-term goals", "text": "A ₹10 lakh higher-education goal in 2027 needs about ₹11.6 lakh at 2030 prices if inflation runs 5%. Always inflate distant goals when setting the target."},
        ],
    },
    {
        "id": "tax-regimes-fy-2025-26",
        "category": "Taxation",
        "title": "New vs Old tax regime — FY 2025-26",
        "read_minutes": 7,
        "tags": ["tax", "planning"],
        "art_variant": "mint",
        "why_relevant": "Choosing correctly can save you tens of thousands in tax each year.",
        "body": [
            {"heading": "The two regimes", "text": "For AY 2026-27 (FY 2025-26) salaried Indians can choose the new regime (default) or the old regime. The new regime has broader slabs and a lower headline rate but disallows most deductions. The old regime has narrower slabs but lets you claim 80C, HRA, home-loan interest and more."},
            {"heading": "New regime slabs (FY 2025-26)", "text": "0% up to ₹4 lakh · 5% ₹4-8 lakh · 10% ₹8-12 lakh · 15% ₹12-16 lakh · 20% ₹16-20 lakh · 25% ₹20-24 lakh · 30% above ₹24 lakh. Standard deduction of ₹75,000 for salaried applies. Rebate under section 87A makes income up to ₹12 lakh effectively tax-free for many salaried employees. Always verify the latest slabs on incometax.gov.in — rules can be revised in the annual budget."},
            {"heading": "Old regime — quick recap", "text": "0% up to ₹2.5 lakh · 5% up to ₹5 lakh · 20% up to ₹10 lakh · 30% above ₹10 lakh. Combine with 80C (₹1.5 lakh), 80D (₹25-75k), HRA, home-loan interest, NPS 80CCD(1B) ₹50k."},
            {"heading": "Which one should you pick?", "text": "If your legitimate deductions add up to less than ~₹4 lakh, the new regime is usually better. If you use HRA + home loan + 80C + 80D fully, the old regime often wins. Use the official IT department calculator each year — the crossover point shifts with slab changes."},
        ],
    },
    {
        "id": "sip-vs-lump-sum",
        "category": "Investing basics",
        "title": "SIP vs Lumpsum — which is right for you?",
        "read_minutes": 5,
        "tags": ["sip", "investing", "strategy"],
        "art_variant": "dark",
        "why_relevant": "The vehicle you choose can meaningfully affect returns.",
        "body": [
            {"heading": "SIP in one line", "text": "A fixed amount invested at fixed intervals (usually monthly). Automated, disciplined, and forgiving of bad market timing."},
            {"heading": "When lumpsum wins", "text": "If you receive a bonus or inheritance and the market is meaningfully undervalued, a lumpsum captures the entire rally. But timing the market is genuinely difficult — most retail investors do better with SIP + STP (systematic transfer plan)."},
            {"heading": "The hybrid approach", "text": "Park the lumpsum in a liquid fund and set up an STP to move a fixed sum into equity every month. You keep the compounding benefit while spreading the entry."},
        ],
    },
]

DAILY_TIPS = [
    {"kind": "Today's Financial Fact", "text": "The average Indian saves about 30% of income — but experts recommend at least 20% goes toward long-term investing, not just parking in a savings account."},
    {"kind": "Today's Tax Tip", "text": "Contributions to NPS (Tier 1) qualify for an additional ₹50,000 deduction under section 80CCD(1B) in the old regime — over and above the ₹1.5 lakh 80C limit."},
    {"kind": "Today's Investment Concept", "text": "SIP + STP: park a windfall in a liquid fund, then use a Systematic Transfer Plan to drip it into equity every month. You get compounding without market-timing anxiety."},
    {"kind": "Today's Money Mistake to Avoid", "text": "Buying insurance for tax saving. ULIPs and endowment plans usually give sub-inflation returns. Keep insurance and investment separate: term plan + mutual funds."},
    {"kind": "Today's Financial Term", "text": "Expense ratio — the annual fee a mutual fund charges as a % of your investment. Direct plans have lower expense ratios than Regular plans because they cut out distributor commissions."},
    {"kind": "Today's Financial Fact", "text": "Since 1 April 2023 the debt mutual fund gains lost indexation benefit — they're now taxed at your slab rate regardless of holding period."},
    {"kind": "Today's Tax Tip", "text": "Health insurance premiums for parents give an extra ₹25,000 (or ₹50,000 if senior) deduction under 80D, over and above your own family cover."},
    {"kind": "Today's Investment Concept", "text": "Rupee-cost averaging: investing a fixed sum monthly automatically buys more units when prices fall and fewer when they rise — it lowers your average cost."},
    {"kind": "Today's Money Mistake to Avoid", "text": "Chasing last year's best-performing mutual fund. Past returns don't predict future returns, and the top-performer often reverts to average."},
    {"kind": "Today's Financial Term", "text": "STCG vs LTCG — Short-Term Capital Gains (equity: <12 months, tax 20%) vs Long-Term Capital Gains (equity: >12 months, tax 12.5% above ₹1.25 lakh per year for FY 2025-26)."},
    {"kind": "Today's Scam Awareness", "text": "RBI never asks for your OTP, PIN or full card number over call or WhatsApp. Any 'refund' or 'blocked account' call demanding these details is a scam."},
    {"kind": "Today's Financial Concept", "text": "The 50-30-20 rule: 50% of after-tax income to needs, 30% to wants, 20% to savings & debt repayment. A useful starting frame, not a rigid law."},
    {"kind": "Today's Tax Tip", "text": "The Standard Deduction of ₹75,000 (new regime) or ₹50,000 (old) is automatic for salaried employees — you don't need proof to claim it."},
    {"kind": "Today's Investment Concept", "text": "Asset allocation matters more than fund selection. Getting 60/40 equity-debt roughly right beats obsessing over which small-cap fund to pick."},
]


def build_learn_router(db: AsyncIOMotorDatabase, get_current_user):
    router = APIRouter(prefix="/learn", tags=["learn"])

    @router.get("/articles")
    async def list_articles():
        return {"articles": [
            {k: v for k, v in a.items() if k != "body"} | {"summary": a["body"][0]["text"][:180] + "…"}
            for a in LEARN_ARTICLES
        ]}

    @router.get("/articles/{article_id}")
    async def get_article(article_id: str):
        art = next((a for a in LEARN_ARTICLES if a["id"] == article_id), None)
        if not art:
            raise HTTPException(404, "Article not found.")
        return art

    @router.get("/daily")
    async def daily():
        # Deterministic: same day → same tip
        today = date.today().isoformat()
        idx = int(hashlib.sha1(today.encode()).hexdigest(), 16) % len(DAILY_TIPS)
        tip = DAILY_TIPS[idx]
        return {
            "date": today,
            "kind": tip["kind"],
            "text": tip["text"],
            "index": idx,
            "of_total": len(DAILY_TIPS),
        }

    return router


# --------- WHAT-IF SIMULATOR ----------

class WhatIfInput(BaseModel):
    current_monthly_savings: float = Field(ge=0)
    monthly_savings_delta: float = 0  # positive = save more
    monthly_income: Optional[float] = None
    monthly_expenses: Optional[float] = None
    goal_target: float = Field(gt=0)
    goal_current: float = Field(ge=0, default=0)
    expected_annual_return: float = Field(ge=0, le=40, default=8.0)
    years_horizon: Optional[int] = None


def _months_to_goal(current: float, monthly_save: float, target: float, annual_rate_pct: float) -> Optional[int]:
    if monthly_save <= 0:
        return None
    r = annual_rate_pct / 100.0 / 12.0
    balance = current
    for m in range(1, 12 * 60 + 1):  # cap at 60 years
        balance = balance * (1 + r) + monthly_save
        if balance >= target:
            return m
    return None


def _project_balance(current: float, monthly_save: float, months: int, annual_rate_pct: float) -> float:
    r = annual_rate_pct / 100.0 / 12.0
    balance = current
    for _ in range(months):
        balance = balance * (1 + r) + monthly_save
    return round(balance, 2)


def build_whatif_router(get_current_user, db: Optional[AsyncIOMotorDatabase] = None):
    router = APIRouter(prefix="/whatif", tags=["whatif"])

    @router.post("")
    async def whatif(body: WhatIfInput, user=Depends(get_current_user)):
        base = body.current_monthly_savings
        proposed = max(0.0, base + body.monthly_savings_delta)
        rate = body.expected_annual_return
        months_base = _months_to_goal(body.goal_current, base, body.goal_target, rate)
        months_new = _months_to_goal(body.goal_current, proposed, body.goal_target, rate)
        # Build a projection series for chart (36 months by default)
        horizon = min(360, (body.years_horizon or 5) * 12)
        series = []
        r = rate / 100.0 / 12.0
        b_base = body.goal_current
        b_new = body.goal_current
        for m in range(1, horizon + 1):
            b_base = b_base * (1 + r) + base
            b_new = b_new * (1 + r) + proposed
            if m % 3 == 0 or m == horizon:
                series.append({"month": m, "current": round(b_base, 0), "proposed": round(b_new, 0)})
        return {
            "current_monthly_savings": base,
            "proposed_monthly_savings": proposed,
            "months_to_goal_current": months_base,
            "months_to_goal_proposed": months_new,
            "goal_target": body.goal_target,
            "goal_current": body.goal_current,
            "expected_annual_return": rate,
            "projection_current": _project_balance(body.goal_current, base, horizon, rate),
            "projection_proposed": _project_balance(body.goal_current, proposed, horizon, rate),
            "horizon_months": horizon,
            "series": series,
            "disclaimer": "Projections are educational estimates only. Actual returns depend on market conditions and are not guaranteed. Consult a qualified financial advisor for personalised planning.",
        }

    # ---------- AI PURCHASE SCENARIO ----------

    @router.post("/scenario")
    async def scenario(body: ScenarioInput, user=Depends(get_current_user)):
        """Analyse a hypothetical purchase across 4 outcomes.

        Reads the user's actual finances + goals (never mutates them) and
        computes deterministic financial impact per option, then asks the
        LLM to add reasoning + a best recommendation.
        """
        if db is None:
            raise HTTPException(500, "Simulator not initialised.")
        uid = str(user["_id"])
        # 1. Snapshot user financial state (read-only)
        profile = user.get("profile") or {}
        income = int(profile.get("monthly_income", 0) or 0)
        expenses = int(profile.get("monthly_expenses", 0) or 0)
        savings = int(profile.get("current_savings", 0) or 0)
        investments = int(profile.get("investments", 0) or 0)
        emi = int(profile.get("emi", 0) or 0)
        monthly_free_cash = max(0, income - expenses - emi)

        raw_goals = [
            {k: v for k, v in g.items() if k not in ("_id",)}
            for g in await db.finaura_goals.find({"user_id": uid}).to_list(200)
        ]

        options = [
            _compute_scenario_option("buy_now", body, savings, monthly_free_cash, raw_goals, months_delay=0),
            _compute_scenario_option("after_3m", body, savings, monthly_free_cash, raw_goals, months_delay=3),
            _compute_scenario_option("after_6m", body, savings, monthly_free_cash, raw_goals, months_delay=6),
        ]

        # 2. Ask the LLM to reason over the deterministic options and pick a best one
        ai = await _ai_recommendation(body, options, {
            "income": income, "expenses": expenses, "savings": savings,
            "investments": investments, "emi": emi, "free_cash": monthly_free_cash,
        }, raw_goals)

        # 3. Attach AI reasoning to each option, then build a synthesised best_option
        by_id = {o["id"]: o for o in options}
        for reasoning in ai.get("option_analysis", []):
            oid = reasoning.get("id")
            if oid in by_id:
                by_id[oid]["ai_note"] = reasoning.get("note", "")
                by_id[oid]["pros"] = reasoning.get("pros", by_id[oid].get("pros", []))
                by_id[oid]["cons"] = reasoning.get("cons", by_id[oid].get("cons", []))

        best_id = ai.get("best_option_id") or "after_3m"
        best_option = dict(by_id.get(best_id, options[1]))
        best_option["id"] = "best"
        best_option["label"] = f"AI Best — {best_option.get('label', '').replace(' (AI Best)', '')}"
        best_option["ai_recommendation"] = ai.get("recommendation", "")
        best_option["ai_reasoning"] = ai.get("reasoning", "")

        return {
            "scenario": body.model_dump(),
            "user_snapshot": {
                "monthly_income": income, "monthly_expenses": expenses,
                "current_savings": savings, "investments": investments,
                "emi": emi, "monthly_free_cash": monthly_free_cash,
                "goal_count": len(raw_goals),
            },
            "options": options + [best_option],
            "ai_available": ai.get("ai_available", False),
            "disclaimer": "This is a hypothetical simulation. No real financial data is modified. AI analysis is educational and not personalised financial advice.",
        }

    @router.post("/scenario/apply")
    async def apply_scenario(body: ApplyScenarioInput, user=Depends(get_current_user)):
        """Pin the chosen scenario as a long-term memory so the AI chat remembers it.
        This does NOT mutate goals, transactions or balances."""
        if db is None:
            raise HTTPException(500, "Simulator not initialised.")
        uid = str(user["_id"])
        now = datetime.now(timezone.utc)
        key = f"whatif_plan_{body.scenario_name[:60].strip().lower().replace(' ', '_')}_{int(now.timestamp())}"
        summary = (
            f"User has chosen the '{body.option_label}' plan for '{body.scenario_name}' "
            f"(₹{body.amount:,}). {body.summary}"
        )
        await db.finaura_memories.insert_one({
            "id": key,
            "user_id": uid,
            "category": "plan",
            "key": key,
            "value": summary,
            "numeric_value": body.amount,
            "unit": "INR",
            "source": "whatif_simulator",
            "created_at": now,
            "updated_at": now,
        })
        return {"pinned": True, "memory_id": key}

    return router


# ---------- SCENARIO HELPERS ----------

class ScenarioInput(BaseModel):
    item_name: str = Field(min_length=1, max_length=120)
    amount: int = Field(gt=0, le=10_00_00_000)  # 10 crore ceiling
    category: Optional[str] = None
    recurring_monthly_cost: Optional[int] = Field(default=None, ge=0, le=10_00_000)
    purchase_date: Optional[str] = None  # ISO date; informational
    notes: Optional[str] = Field(default=None, max_length=500)


class ApplyScenarioInput(BaseModel):
    scenario_name: str = Field(min_length=1, max_length=120)
    amount: int = Field(gt=0)
    option_label: str
    summary: str = Field(max_length=800)


def _compute_scenario_option(oid: str, body: ScenarioInput, savings: int, monthly_free_cash: int,
                             goals: list, months_delay: int) -> dict:
    """Deterministic financial impact of the given purchase timing."""
    labels = {
        "buy_now": "Buy Now",
        "after_3m": "After 3 Months",
        "after_6m": "After 6 Months",
    }
    label = labels.get(oid, oid)
    # Savings the user will accumulate before purchase (assumes all free cash saved during wait)
    saved_before_purchase = monthly_free_cash * months_delay
    cash_available_at_purchase = savings + saved_before_purchase
    price = body.amount
    recurring = body.recurring_monthly_cost or 0

    # Cash impact
    remaining_cash = cash_available_at_purchase - price
    dips_into_savings = price > cash_available_at_purchase - savings if months_delay == 0 else remaining_cash < savings * 0.5

    # Goal impact — approximate months of delay per active goal
    # Formula: (price - savings_you_would_have_used_from_free_cash) / monthly_contribution
    goal_impacts = []
    for g in goals:
        monthly = int(g.get("monthly_contribution") or 0)
        current = int(g.get("current_amount") or 0)
        target = int(g.get("target_amount") or 0)
        if target <= current:
            continue
        # How much of this goal's future contributions the purchase effectively consumes
        drain = max(0, price - saved_before_purchase)  # what the purchase pulls from savings pool
        # Distribute drain across goals by priority weight
        prio_weight = {"High": 1.5, "Medium": 1.0, "Low": 0.6}.get(g.get("priority", "Medium"), 1.0)
        weighted_drain = drain * prio_weight / max(1, len(goals))
        months_added = int(round(weighted_drain / monthly)) if monthly > 0 else 0
        if months_added > 0:
            goal_impacts.append({
                "goal_id": g.get("id"),
                "goal_name": g.get("name"),
                "priority": g.get("priority", "Medium"),
                "months_delay": months_added,
            })

    total_goal_delay = sum(gi["months_delay"] for gi in goal_impacts)

    # Health score effect (rough, additive) — for display
    health_delta = 0
    if dips_into_savings:
        health_delta -= 8
    if recurring:
        health_delta -= min(6, int(recurring / max(1, monthly_free_cash) * 10))
    if months_delay > 0 and not dips_into_savings:
        health_delta += 3

    pros, cons = [], []
    if months_delay == 0:
        pros.append("Immediate — you get the item today.")
        if dips_into_savings:
            cons.append("Uses a large share of your current savings.")
        else:
            pros.append("Fits within your current cash without touching long-term savings.")
        if goal_impacts:
            cons.append(f"May delay {len(goal_impacts)} goal(s) by ~{total_goal_delay} month(s) in total.")
    else:
        pros.append(f"You save ~₹{saved_before_purchase:,} of free cash before buying.")
        if remaining_cash > savings * 0.5:
            pros.append("Preserves at least half of your existing savings buffer.")
        else:
            cons.append("Still puts a meaningful dent in your savings.")
        if total_goal_delay < 3:
            pros.append("Very small impact on your active goals.")
        elif goal_impacts:
            cons.append(f"Goals may still slip by ~{total_goal_delay} month(s).")
    if recurring:
        cons.append(f"Adds ₹{recurring:,}/mo recurring cost.")

    return {
        "id": oid,
        "label": label,
        "months_delay": months_delay,
        "cash_available_at_purchase": cash_available_at_purchase,
        "remaining_cash_after_purchase": remaining_cash,
        "dips_into_savings": bool(dips_into_savings),
        "recurring_monthly_cost": recurring,
        "total_recurring_first_year": recurring * 12,
        "goal_impacts": goal_impacts,
        "total_goal_delay_months": total_goal_delay,
        "health_score_delta": health_delta,
        "pros": pros,
        "cons": cons,
    }


async def _ai_recommendation(body: ScenarioInput, options: list, snapshot: dict, goals: list) -> dict:
    """Ask Claude Sonnet 5 to reason over the deterministic options and pick the best one.
    Falls back gracefully if the LLM is unavailable — a rule-based best pick is returned."""
    fallback_best_id = _rule_based_best(options)
    fallback = {
        "ai_available": False,
        "best_option_id": fallback_best_id,
        "recommendation": _fallback_recommendation(options, fallback_best_id, body),
        "reasoning": "Automatic recommendation based on cash buffer and goal impact.",
        "option_analysis": [],
    }
    key = os.environ.get("EMERGENT_LLM_KEY")
    if not key:
        return fallback
    try:
        from emergentintegrations.llm.chat import LlmChat, UserMessage
    except Exception:
        return fallback

    goal_lines = "; ".join(
        f"{g.get('name')} (priority={g.get('priority')}, target=₹{g.get('target_amount',0):,}, "
        f"current=₹{g.get('current_amount',0):,}, monthly=₹{g.get('monthly_contribution',0):,})"
        for g in goals[:10]
    ) or "no active goals"

    option_json = [{
        "id": o["id"], "label": o["label"], "months_delay": o["months_delay"],
        "cash_after": o["remaining_cash_after_purchase"],
        "dips_into_savings": o["dips_into_savings"],
        "total_goal_delay_months": o["total_goal_delay_months"],
        "recurring": o["recurring_monthly_cost"],
        "health_delta": o["health_score_delta"],
    } for o in options]

    system = (
        "You are FINAURA AI's What-If financial simulator. "
        "Given a user's real Indian personal-finance snapshot and three purchase-timing options that were "
        "computed deterministically, do TWO things: (1) enrich each option with a short note (max 30 words), "
        "2 pros, 2 cons; (2) pick exactly one best_option_id ('buy_now' | 'after_3m' | 'after_6m') and write "
        "a 2-3 sentence recommendation plus 1-2 sentence reasoning. Be specific to the user's data. "
        "Amounts in INR. Never invent numbers not present in the inputs. Reply with STRICT JSON only, no prose."
    )
    prompt = (
        f"User snapshot: {json.dumps(snapshot)}\n"
        f"Goals: {goal_lines}\n"
        f"Purchase: {body.item_name} for ₹{body.amount:,}"
        + (f" (recurring ₹{body.recurring_monthly_cost:,}/mo)" if body.recurring_monthly_cost else "")
        + (f", category={body.category}" if body.category else "")
        + (f", target date={body.purchase_date}" if body.purchase_date else "")
        + "\n"
        f"Options: {json.dumps(option_json)}\n\n"
        'JSON schema: {"best_option_id": "buy_now|after_3m|after_6m",'
        ' "recommendation": "...", "reasoning": "...",'
        ' "option_analysis": [{"id": "buy_now|after_3m|after_6m", "note": "...",'
        '   "pros": ["...","..."], "cons": ["...","..."]}]}'
    )
    try:
        chat = LlmChat(api_key=key, session_id=f"whatif-{int(datetime.now().timestamp())}",
                       system_message=system).with_model("anthropic", "claude-sonnet-5")
        resp = await chat.send_message(UserMessage(text=prompt))
        text = getattr(resp, "text", None) or str(resp)
        # Extract JSON if wrapped in code fences
        s = text.strip()
        if s.startswith("```"):
            s = s.strip("`")
            if s.lower().startswith("json"):
                s = s[4:]
            s = s.strip()
        # Locate outer braces
        first = s.find("{"); last = s.rfind("}")
        if first < 0 or last < 0:
            raise ValueError("no json object")
        parsed = json.loads(s[first : last + 1])
        best = parsed.get("best_option_id")
        if best not in {"buy_now", "after_3m", "after_6m"}:
            best = fallback_best_id
        return {
            "ai_available": True,
            "best_option_id": best,
            "recommendation": (parsed.get("recommendation") or "")[:600],
            "reasoning": (parsed.get("reasoning") or "")[:600],
            "option_analysis": parsed.get("option_analysis") or [],
        }
    except Exception as exc:
        log.warning("AI What-If recommendation failed: %s", exc)
        return fallback


def _rule_based_best(options: list) -> str:
    """Pick the option with the smallest goal-delay while keeping some savings."""
    def score(o: dict) -> tuple:
        # lower is better
        return (o["total_goal_delay_months"], -o["remaining_cash_after_purchase"], o["months_delay"])
    return min(options, key=score)["id"]


def _fallback_recommendation(options: list, best_id: str, body: ScenarioInput) -> str:
    opt = next((o for o in options if o["id"] == best_id), options[0])
    return (
        f"Based on your cash buffer and current goals, buying '{body.item_name}' "
        f"{opt['label'].lower()} keeps your goals closest to schedule and preserves the most cash. "
        f"Estimated goal delay: {opt['total_goal_delay_months']} months."
    )
