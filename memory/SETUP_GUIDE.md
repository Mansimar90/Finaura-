# Finaura — External Service Setup Guide

You wanted to know how to obtain the OAuth and Resend credentials. Here's a step-by-step guide for each. **You don't need to code anything** — just follow the steps, copy each value into the environment file mentioned at the bottom, then restart the backend.

Your app is deployed at: **`https://wealth-insights-43.preview.emergentagent.com`**

---

## 1. Google OAuth (Authorization Code flow — server-side token exchange)

**Time to complete: ~5 minutes.** No billing account required.

1. Go to <https://console.cloud.google.com/> and sign in with any Google account.
2. **Create a project** (top-left project dropdown → "New Project"): name it `Finaura`. Wait ~10s for it to be created.
3. In the left sidebar go to **APIs & Services → OAuth consent screen**.
   - Choose **External** and click Create.
   - App name: `Finaura`. Support email: your own email.
   - Developer contact: your own email.
   - Skip "Scopes" and "Test users" (Save & Continue on each). Publish is optional — while in "Testing" only test users can sign in; add your own email under "Test users".
4. Left sidebar → **APIs & Services → Credentials → Create Credentials → OAuth client ID**.
   - Application type: **Web application**
   - Name: `Finaura Web`
   - **Authorised JavaScript origins** (add both):
     - `https://wealth-insights-43.preview.emergentagent.com`
     - `http://localhost:3000` (only if you'll test locally)
   - **Authorised redirect URIs** — add exactly this URL:
     - `https://wealth-insights-43.preview.emergentagent.com/api/auth/google/callback`
   - Click **Create**.
5. Copy the **Client ID** AND **Client Secret** (both are shown once).
6. Paste them into `/app/backend/.env`:
   ```
   GOOGLE_CLIENT_ID="1234-abcxyz.apps.googleusercontent.com"
   GOOGLE_CLIENT_SECRET="GOCSPX-xxxxxxxxxxxxxxxx"
   GOOGLE_REDIRECT_URI="https://wealth-insights-43.preview.emergentagent.com/api/auth/google/callback"
   ```
   And also add the **Client ID only** (public) to `/app/frontend/.env`:
   ```
   REACT_APP_GOOGLE_CLIENT_ID=1234-abcxyz.apps.googleusercontent.com
   ```
7. Restart services: `sudo supervisorctl restart backend frontend`.
8. Refresh the login page — the "Continue with Google" button will now appear.

Notes:
- The **Client Secret** is stored server-side only (`backend/.env`). It never touches the browser.
- If you change the preview URL (redeploy or rebrand), you must add the new origin **and** the new redirect URI in step 4.
- If you attach a custom domain, add `https://your-domain.com` to Authorized JS Origins and `https://your-domain.com/api/auth/google/callback` to Authorized Redirect URIs.

---

## 2. Apple Sign-In

**Time to complete: ~30 minutes.** Requires an **Apple Developer account ($99/year)** — Apple charges everyone, no exceptions. If you don't have one yet, Google Sign-In alone is fine to launch with.

You need to create four things: **Services ID**, **Team ID**, **Key ID**, and a **.p8 private key file**.

1. Sign in at <https://developer.apple.com/account/>.
2. **Certificates, Identifiers & Profiles** → **Identifiers**:
   - Click ➕ → choose **App IDs** → **App** → Continue.
     - Description: `Finaura App`. Bundle ID: `com.yourdomain.finaura` (any reverse-domain works; you own it).
     - Capabilities: check **Sign In with Apple** → Save. Note this Bundle ID.
   - Click ➕ → choose **Services IDs** → Continue.
     - Description: `Finaura Web`. Identifier: `com.yourdomain.finaura.web`.
     - Enable **Sign In with Apple** → Configure:
       - **Primary App ID**: the App ID you just made.
       - **Domains and Subdomains**: `wealth-insights-43.preview.emergentagent.com`
       - **Return URLs**: `https://wealth-insights-43.preview.emergentagent.com/auth/apple/callback`
       - Save → Continue → Register.
   - **This Services ID string** (e.g. `com.yourdomain.finaura.web`) is your `APPLE_CLIENT_ID`.
3. **Keys** (left sidebar) → ➕
   - Key Name: `Finaura Sign In with Apple`
   - Check **Sign In with Apple** → Configure → choose the App ID from step 2 → Save → Continue → Register.
   - **Download the .p8 file NOW** — Apple never shows it again. Store it safely.
   - The Key ID (10-character alphanumeric) shown on this page is your `APPLE_KEY_ID`.
4. Your **Team ID** is at the top of every developer page (e.g. `AB12CD34EF`). That's `APPLE_TEAM_ID`.
5. Open the downloaded `.p8` file in a text editor. Copy its full contents (including the `-----BEGIN PRIVATE KEY-----` and `-----END PRIVATE KEY-----` lines).
6. Paste into `/app/backend/.env`:
   ```
   APPLE_CLIENT_ID="com.yourdomain.finaura.web"
   APPLE_TEAM_ID="AB12CD34EF"
   APPLE_KEY_ID="XYZ1234567"
   APPLE_PRIVATE_KEY="-----BEGIN PRIVATE KEY-----\nMIGT...\n-----END PRIVATE KEY-----"
   ```
   ⚠️ Replace real newlines with `\n` (single-line in .env) OR keep it on a single line without newlines — Python will still parse it. Easier: paste the whole .p8 into a single `"..."` string keeping the `\n` escape sequences.
7. Also add the client ID to `/app/frontend/.env`:
   ```
   REACT_APP_APPLE_CLIENT_ID=com.yourdomain.finaura.web
   ```
8. Restart: `sudo supervisorctl restart backend frontend`.

---

## 3. Resend (Email delivery)

**Time to complete: ~10 minutes.** Free tier: 100 emails/day, 3000/month.

1. Sign up at <https://resend.com/>. Use any email; verify it in your inbox.
2. **Verify a sending domain** (this is Resend's biggest gotcha — you *cannot* send from `@gmail.com`, `@outlook.com`, etc.):
   - Dashboard → **Domains** → Add Domain → enter e.g. `finaura.app` (a domain you own).
   - Add the DNS records Resend gives you (MX, TXT, DKIM) at your DNS provider (GoDaddy, Namecheap, Cloudflare, etc.). Usually takes 5–30 minutes to verify.
   - Once you see **✅ Verified**, move on.
   - **No domain yet?** For testing you can use Resend's sandbox: set `RESEND_FROM_EMAIL="Finaura <onboarding@resend.dev>"` — but emails can only go to the address you signed up with.
3. **Create an API key**:
   - Dashboard → **API Keys** → Create API Key
   - Name: `Finaura Production`. Permission: **Sending access** (least privilege).
   - Copy the key immediately — it starts with `re_` and is only shown once.
4. Paste into `/app/backend/.env`:
   ```
   RESEND_API_KEY="re_XXXXXXXXXXXXXXXX"
   RESEND_FROM_EMAIL="Finaura <noreply@finaura.app>"
   ```
5. Restart backend: `sudo supervisorctl restart backend`.
6. Test: go to `/forgot-password`, enter your account email. Check your inbox — a real Finaura email arrives within ~10 seconds. (Until you set the key, the reset link is printed to `/var/log/supervisor/backend.err.log` instead.)

---

## Quick recap — the env values

**`/app/backend/.env`** (already exists, only edit the empty values):
```
GOOGLE_CLIENT_ID="1234-abcxyz.apps.googleusercontent.com"
APPLE_CLIENT_ID="com.yourdomain.finaura.web"
APPLE_TEAM_ID="AB12CD34EF"
APPLE_KEY_ID="XYZ1234567"
APPLE_PRIVATE_KEY="-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----"
APPLE_REDIRECT_URI="https://wealth-insights-43.preview.emergentagent.com/auth/apple/callback"
RESEND_API_KEY="re_XXXXXXXXXXXXXXXX"
RESEND_FROM_EMAIL="Finaura <noreply@finaura.app>"
```

**`/app/frontend/.env`** (already exists, only edit the empty values):
```
REACT_APP_GOOGLE_CLIENT_ID=1234-abcxyz.apps.googleusercontent.com
REACT_APP_APPLE_CLIENT_ID=com.yourdomain.finaura.web
```

After editing either file:
```bash
sudo supervisorctl restart backend frontend
```

That's it. The Google / Apple buttons appear on `/login` and `/signup` automatically once their client IDs are present, and password-reset emails start being delivered as soon as the Resend key is set.
