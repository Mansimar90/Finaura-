# Finaura Product Requirements Document

## Original Problem Statement
Build Finaura — AI-Powered Personal Financial Intelligence Platform: a premium, clickable MVP that transforms manually entered or uploaded fictional financial information into a clear view of financial health, spending, goals, changes, and personalized financial education, without requiring bank-account connections. Include Dashboard, My Finances, Statements, Six-Month Analysis, Goals & Priorities, Financial Changes, Finaura Learn, Ask Finaura, Settings & Privacy, Aarav Sharma demo data, live contextual AI, persistence, and prototype privacy controls.

## Architecture Decisions
- React frontend with BrowserRouter, Recharts, Lucide icons, and CSS variables matching the warm ivory / charcoal / mint design system.
- FastAPI backend with MongoDB via the protected MONGO_URL; financial overview, goals, transaction edits, delete-data, and streaming chat endpoints are under /api.
- Demo data is seeded in-memory with persisted goals and transactions initialized in MongoDB on first overview read. MongoDB responses exclude _id.
- Ask Finaura uses server-side emergentintegrations streaming with the configured EMERGENT_LLM_KEY and an educational-only system prompt.
- The UI is intentionally labeled Demo Data and prototype privacy/security language avoids production security claims.

## User Personas
- Aarav Sharma: a 29-year-old product designer who wants a calm, complete picture of money without connecting a bank account.
- Future privacy-conscious users who manually provide statements, review categorization, and use education to make better-informed decisions.

## Core Requirements (Static)
1. Understand monthly income, expenses, savings, net worth, debt, investments, EMI, health score, and spending categories.
2. Import or simulate statements, review categorized transactions, and correct categories.
3. Analyze six months of income, expenses, savings, and rates.
4. Create and prioritize financial goals with progress and monthly requirements.
5. Explain meaningful financial changes through alerts and a timeline.
6. Personalize financial education without issuing investment orders.
7. Answer questions using the user profile and app data.
8. Do not require bank connections; clearly label fictional demo data.
9. Provide privacy notice, security settings, and deletion control.

## Implemented (2026-08-20)
- Premium responsive Finaura shell with nine route-based workspaces and mobile navigation.
- Dashboard with summary metrics, cash-flow area chart, 78/100 health breakdown, goals, and insight.
- My Finances allocation donut and cash/investment/debt/EMI view.
- Statements upload simulation, review banner, editable transaction categories, and persisted category updates.
- Six-Month Analysis bar chart and monthly breakdown.
- Goals cards with progress/priority and persisted New Goal modal with friendly error state.
- Financial Changes alert and March–August What Changed timeline.
- Finaura Learn personalized feature recommendation plus topic cards.
- Ask Finaura streaming contextual AI chat with educational disclaimer.
- Settings & Privacy prototype controls and delete-my-financial-data action.
- Backend persistence and ObjectId-safe API responses.

## Prioritized Backlog
### P0
- Replace demo profile storage with authenticated, per-user encrypted storage before real financial data.
- Add production-grade document parsing/OCR with explicit review and confirmation states.

### P1
- Add goal conflict calculator and what-if monthly contribution scenarios.
- Add richer transaction entry and CSV/Excel import validation.
- Add chat history persistence and user-visible conversation export/delete.

### P2
- Add advanced financial digital twin and early-warning signals.
- Add expanded Learn library with progress tracking and explainers.

## Next Tasks
- Validate the end-to-end encryption and key-isolation design with a security review before real data.
- Add authenticated user profiles and replace the single Aarav demo persona.
- Connect production document processing only after the review/edit/confirm flow is retained.
