# Finaura — Product Requirements Document

## Original problem statement
Finaura is an AI-powered personal financial intelligence platform that helps users understand and organize their complete financial life without connecting a bank account. Five MVP features: Dashboard, Statement Reader, Goals & Priorities, Financial Changes, and Finaura Learn — plus Ask Finaura AI chat and privacy controls.

## User personas
- **New user** — Signs up with email + password (or Google / Apple), starts empty, can optionally load demo data. Sets a 4-digit PIN and/or passkey.
- **Returning user** — Signs in, then unlocks the app with PIN or passkey.
- **Guest** — Explores `/demo` publicly before signing up.

## Architecture
- Backend (FastAPI + MongoDB via motor):
  - `server.py` — main app, /financial, /demo, /goals, /transactions, /chat, /statements/import-demo.
  - `auth.py` — /auth/register, /login, /logout, /me, /google, /apple, /forgot-password, /reset-password, /verify-email, /set-pin, /verify-pin, /remove-pin, /onboard.
  - `passkeys.py` — /auth/passkey/list, /register/begin+complete, /authenticate/begin+complete, DELETE.
  - `statements.py` — /statements/preview, /parse, /confirm-import (CSV/Excel/PDF).
  - `email_service.py` — Resend + console fallback.
- Frontend (React + React Router + Recharts):
  - `lib/auth.jsx`, `lib/api.js`, `lib/passkey.js`.
  - Auth pages: Login, Signup, ForgotPassword, ResetPassword, VerifyEmail, Onboarding, PinLock.
  - Feature pages: StatementUpload.jsx; the rest live in App.js.
- All finaura_goals + finaura_transactions are scoped by `user_id`. Bearer JWT auth.

## Implemented (Feb 2026 — Phases 1-3)
- Phase 1: Full Finaura MVP (Dashboard, My Finances, Statements, 6-month Analysis, Goals, Changes, Learn, Ask Finaura AI chat).
- Phase 2: Complete auth stack — email/password with bcrypt + JWT, Google + Apple Sign-In (real OAuth, ready for keys), Resend-powered password reset & email verification with console fallback, brute-force lockouts (email-keyed, tz-aware), 4-digit PIN with 5-attempt lockout, onboarding with blank-or-demo choice, multi-tenant data isolation, public `/demo` guest mode.
- Phase 3: **Passkeys** — Face ID / Touch ID / Windows Hello / hardware keys via WebAuthn, alongside PIN. Passkey management in Settings. Unlock button on Lock screen. **Real statement upload** — CSV + Excel with column mapping and 5-row preview, PDF text extraction with DR/CR word-boundary detection, review-then-import step, 10 MB size cap enforced on both /preview and /parse, allowed-list sanitization on category and type at import.
- Phase 4 (Feb 23, 2026): **Google OAuth 2.0 Authorization-Code flow** — new endpoints GET `/api/auth/google/start` and GET `/api/auth/google/callback`. Client secret stored ONLY server-side (`GOOGLE_CLIENT_SECRET` in backend/.env). Server-side token exchange with Google, ID-token verification, minimal profile storage (`google_sub`, email, name, picture). Existing account linking by verified email only (no takeover of password accounts). CSRF via random `state` persisted in `oauth_states` (single-use, TTL 10min) + HttpOnly SameSite=Lax cookie defense-in-depth. Graceful failure branches (cancel, invalid state, expired state, exchange fail, network error) redirect to a dedicated `/auth/google/success` frontend page that displays user-friendly messages. Open-redirect protection on `next` (rejects `//`, backslash, control chars). Legacy `/api/auth/google` ID-token endpoint preserved for backward compatibility. Redirect URI configured: `${FRONTEND_URL}/api/auth/google/callback`.
- Phase 5 (Feb 24, 2026): **Goals reorder + AI What-If Simulator + Tabbed Settings**.
  - **Goals**: added `order` field, `POST /api/goals/reorder`, `PATCH /api/goals/{id}` now accepts partial updates via `GoalPatchInput`. Frontend has drag-and-drop reordering, ↑/↓ arrows, custom styled delete-confirmation modal (replacing native `window.confirm`), defensive fallback when goal document has no priority.
  - **What-If (AI purchase scenarios)**: `POST /api/whatif/scenario` computes 4 outcomes (Buy Now / After 3 Months / After 6 Months / AI Best Recommendation) using the user's real financial snapshot + goals. Claude Sonnet 5 (via `emergentintegrations`) reasons over the deterministic options and picks the best. Rule-based fallback when the LLM is unreachable. `POST /api/whatif/scenario/apply` pins the chosen plan to `finaura_memories` — **never** mutates goals or transactions (proven by tests: goal/transaction counts unchanged before/after).
  - **Settings**: refactored into 6 tabs (Account & Security, Financial Preferences, Notifications, Data Management, Appearance, Privacy). New `GET/PATCH /api/settings/preferences` with defaults + deep-merge; `GET /api/settings/export` returns a full JSON of the user's data (goals, transactions, memories, prefs) — user-isolated. All UI values persist across reload.
- Phase 6 (Feb 24, 2026): **Prominent Goals actions + Subscription-vs-onetime What-If + Digital Twin**.
  - **Goals visibility fix**: replaced the tiny 26px grey icon buttons with a clear **Priority: [High] [Medium] [Low]** chip row (click a chip to change priority instantly, no modal) + a colored 4-button action bar (Up · Down · Edit blue · Delete red). Persists via existing `PATCH /api/goals/{id}`.
  - **Subscription vs one-time**: `POST /api/whatif/subscription` computes total-paid 5y/10y, opportunity cost if invested at 8% p.a., and break-even months. New tab in `/whatif`.
  - **Digital Twin**: `POST /api/whatif/twin` projects net worth 10 years out (with waypoints), plus 3 boost scenarios (save 25% more, +₹5k income, optional lump sum). New `/twin` page with Recharts line chart + scenario cards.
- Phase 7 (Feb 24, 2026): **UPI Statement Reader + Bank/UPI Cross-Verification**.
  - **UPI-aware parsing** in `statements.py`: `detect_upi_app()` for Google Pay / PhonePe / Paytm / BHIM / AmazonPay / CRED; `extract_upi_meta()` pulls UPI ID (`something@ybl`), UTR/ref (9-22 digit run) and merchant name from narration. `_auto_map_columns` recognises "UPI Ref", "VPA", "Payee", etc. Positive amount + no type hint + merchant column → auto-classified as Expense. Category derives from merchant when present. `_detect_type` recognises "Sent"/"Received" in addition to CR/DR.
  - **New `source` field on transactions** (`bank` | `upi`) — added to `ConfirmImportInput`, all `/preview` `/parse` `/confirm-import` endpoints accept `source=upi`. UPI-only fields (`merchant`, `upi_id`, `upi_ref`, `txn_id`, `upi_app`) persisted when present.
  - **Cross-verification**: `GET /api/statements/verify` matches bank vs UPI by amount + type + ±3-day date + UPI-ref-in-description + token overlap. Returns verified_matches (≥0.85), possible_matches (0.6-0.85), upi_only, bank_only, per-month coverage. Never leaks internal `verified_bank_ids`/`matched_bank_ids` or Mongo internals.
  - **Analytics dedupe**: `dedupe_across_sources()` in `statements.py` shared by `/financial/overview` and the verify view — uses the SAME matcher, so a UPI/bank pair with a 1-3 day settlement lag is counted ONCE. Only VERIFIED matches (score ≥ 0.85) are auto-deduped; possible matches stay in both statements and only surface in the /verify view for the user to resolve.
  - **POST /api/statements/resolve-duplicate**: user-driven merge (Pydantic `ResolveDuplicateInput`, verifies both docs belong to caller before mutating). Deletes one side, tags survivor `verified=true`. 422 on missing fields, 400 on same-id, 404 on foreign ids (no partial deletion).
  - **Frontend `/statements`**: new tabs Bank | UPI | Cross-verification. UPI upload dropzone. Verification view shows 6 status cards, monthly coverage badges, verified/possible match rows with "Keep UPI"/"Keep bank" actions, plus UPI-only / Bank-only lists.
  - **Testing**: 132/132 backend tests pass across the full suite (test_iteration14_upi.py, test_iteration15_dedupe.py, test_iteration16_dedupe_boundary.py + prior regression suites).
- Phase 8 (Feb 25, 2026): **Financial data accuracy hardening** — user reported real-money impact bugs.
  - **Strict Credit/Debit classification**: `parse_csv` now applies the "positive unsigned Amount defaults to Expense unless an income keyword is present" heuristic to BOTH `source=bank` and `source=upi` when there is no Debit/Credit column and no Type/CR/DR hint. This stops everyday debits (Swiggy, BigBasket, etc.) on single-Amount-column bank exports from being misread as Income. Refund / cashback / interest-credit / salary / dividend keywords still classify correctly as Income.
  - **Dedupe over-merge fixed**: `_match_score` now (a) VETOES a match with score 0 when both sides carry a distinct 9-22 digit reference (bank narration ref vs UPI `upi_ref`/`txn_id`) — a conflicting reference proves the rows are different transactions; (b) requires either a UPI merchant name that fully appears in the bank narration OR ≥ 2 distinctive shared tokens for the +0.25 overlap bonus, so unrelated same-amount rows within 3 days no longer auto-merge and silently delete an expense; (c) expanded stop-word list (paid, sent, received, to, from, via, order, etc.).
  - **`_auto_map_columns`**: short aliases `cr`/`dr` now match on word boundaries only, so a `Description` column no longer accidentally maps to Credit.
  - **`DELETE /api/financial/data`**: now also purges `finaura_memories` and resets both `has_demo_data` and `has_real_data`. Individual statement deletion (`DELETE /api/statements/{id}`) already scoped to caller with 404 on foreign/unknown IDs.
  - **Demo purge on real upload**: on the first real (non-demo) `/statements/confirm-import`, all demo-flagged rows/goals are removed and `has_real_data=true`; `/statements/import-demo` then returns 409.
  - **Dark-mode text readability**: global CSS overrides in `App.css` for muted text, borders, and card backgrounds in dark mode.
  - **Testing**: 32/32 iteration-18 P0 tests pass; 18/18 new iteration-19 verify + unit tests pass; 125/125 combined serial run pass. Full report in `/app/test_reports/iteration_19.json`.

## Prioritized backlog
- **P1** — Goal edit/delete UI (backend already supports PATCH/DELETE).
- **P1** — Styled drag-and-drop dropzone in the Statement Upload card (replaces raw native input).
- **P1** — Financial digital twin / What-if simulator.
- **P2** — Advanced AI decision engine + goal-conflict engine.
- **P2** — Multi-currency support.
- **P2** — Server-issued unlock token after PIN/passkey verification (currently unlock state is client-side sessionStorage).
- **P3** — Add a dedicated `Groceries` category (bigbasket, blinkit, zepto, dmart) — currently folded under Shopping.
- **P3** — Migrate iteration14/15/16 test suites off the shared `testuser` to per-class fresh users to fix xdist race flakes.
- **P3** — Split `statements.py` and `server.py` (both ~780 lines) into parsing / matching / router modules.

## External services (need your keys)
Guide: `/app/memory/SETUP_GUIDE.md` — step-by-step for Google OAuth, Apple Sign-In, Resend.

## Test credentials
See `/app/memory/test_credentials.md`.

## Known limitations
- Passkey unlock currently returns `{ok:true}` only — the "unlocked" state is a sessionStorage flag. A malicious client could skip the passkey verify locally. Backing endpoints already require the primary JWT, so this only weakens the second-factor gate on the frontend, not the data isolation.
- PDF parsing is best-effort text extraction; heavily formatted / table-only PDFs may yield fewer rows.
- Google / Apple sign-in buttons only appear once keys are added to `.env`.
