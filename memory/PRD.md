# Finaura — Product Requirements Document

## Original problem statement
Finaura is an AI-powered personal financial intelligence platform that helps users understand and organize their complete financial life without connecting a bank account. Users provide data manually or via uploaded statements. The five MVP features: (1) Personal Financial Life Dashboard, (2) Statement Reader & Segregation, (3) Goals & Priorities, (4) Financial Change & Trend Analysis, (5) Personalized Finaura Learn. Plus Ask Finaura AI chat and privacy controls.

## User personas
- **New user**: Signs up with email + password (or Google/Apple). Starts with empty profile; can optionally load demo data. Sets a 4-digit PIN for app lock.
- **Returning user**: Signs in and unlocks with PIN if configured.
- **Guest / prospect**: Explores `/demo` publicly to see Finaura in action before signing up.

## Architecture
- Backend: FastAPI (`server.py` + `auth.py` + `email_service.py`), MongoDB (motor async), Bearer JWT auth.
- Frontend: React 19 + React Router 7, Recharts, lucide-react. Auth pages under `src/pages/`, auth context under `src/lib/auth.jsx`.
- Data isolation: All `finaura_goals` and `finaura_transactions` documents have `user_id`.
- Integrations: OpenAI (via Emergent LLM key), Google OAuth (`@react-oauth/google` + `google-auth`), Apple Sign-In JS + PyJWT JWKS, Resend email with console fallback.

## Implemented in this session (Feb 2026 — Phase 2: Auth overhaul)
- User registration & login (email + bcrypt hashed passwords, JWT sessions, brute-force lockout).
- Google Sign-In (real OAuth flow; ready for user's `GOOGLE_CLIENT_ID`).
- Apple Sign-In (real OAuth flow with JWKS verification; ready for `APPLE_CLIENT_ID` + key set).
- Password reset flow (Resend email with console fallback so the flow works today without a Resend key).
- Email verification with hashed one-time tokens.
- 4-digit PIN lock (bcrypt-hashed, 5-attempt lockout, keyboard + click pad, first-time set + unlock modes).
- Onboarding: choose blank profile or load demo data into the authenticated account.
- Multi-tenant data scoping (`user_id` on goals & transactions).
- Public `/demo` mode — full app UX with the shared demo profile; mutations disabled with sign-up prompts.
- Sidebar overhaul: shows real user's name, initials, and lock/sign-out actions when authenticated.
- Settings page: shows account info, PIN controls, provider list, sign-out, and data deletion.

## Prioritized backlog
- **P1 — Real file upload parsing** (currently import is simulated demo six-month data).
- **P1 — Financial digital twin / What-if simulator**.
- **P2 — Advanced AI decision engine & goal-conflict engine**.
- **P2 — Passkey / WebAuthn biometric add-on** (real Face ID / Touch ID on supported devices).
- **P2 — Multi-currency support**.

## Test credentials
See `/app/memory/test_credentials.md`.

## Known limitations
- Statement OCR / real PDF+CSV parsing is not implemented — imports use demo data only.
- Google/Apple keys are placeholder; the sign-in buttons only appear after keys are set in `backend/.env` + `frontend/.env`.
- Resend key is placeholder; verification/reset emails print to backend logs until key is added.
