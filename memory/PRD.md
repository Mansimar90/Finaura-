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

## Prioritized backlog
- **P1** — Goal edit/delete UI (backend already supports PATCH/DELETE).
- **P1** — Styled drag-and-drop dropzone in the Statement Upload card (replaces raw native input).
- **P1** — Financial digital twin / What-if simulator.
- **P2** — Advanced AI decision engine + goal-conflict engine.
- **P2** — Multi-currency support.
- **P2** — Server-issued unlock token after PIN/passkey verification (currently unlock state is client-side sessionStorage).

## External services (need your keys)
Guide: `/app/memory/SETUP_GUIDE.md` — step-by-step for Google OAuth, Apple Sign-In, Resend.

## Test credentials
See `/app/memory/test_credentials.md`.

## Known limitations
- Passkey unlock currently returns `{ok:true}` only — the "unlocked" state is a sessionStorage flag. A malicious client could skip the passkey verify locally. Backing endpoints already require the primary JWT, so this only weakens the second-factor gate on the frontend, not the data isolation.
- PDF parsing is best-effort text extraction; heavily formatted / table-only PDFs may yield fewer rows.
- Google / Apple sign-in buttons only appear once keys are added to `.env`.
