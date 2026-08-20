"""Auth module for Finaura — email/password + Google + Apple + PIN + reset flows."""
import os
import hashlib
import secrets
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

import bcrypt
import jwt
import uuid
from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request, Depends
from pydantic import BaseModel, EmailStr, Field
from motor.motor_asyncio import AsyncIOMotorDatabase

from email_service import send_email, verify_email_template, reset_password_template

log = logging.getLogger("finaura.auth")

JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_MINUTES = 60 * 24 * 7  # 7 days
PIN_MAX_ATTEMPTS = 5
PIN_LOCKOUT_MINUTES = 15
LOGIN_MAX_ATTEMPTS = 5
LOGIN_LOCKOUT_MINUTES = 15


# ---------- Password / PIN hashing ----------

def hash_secret(value: str) -> str:
    return bcrypt.hashpw(value.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_secret(plain: str, hashed: str) -> bool:
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


# ---------- JWT ----------

def _jwt_secret() -> str:
    secret = os.environ.get("JWT_SECRET")
    if not secret:
        raise RuntimeError("JWT_SECRET is not configured")
    return secret


def create_access_token(user_id: str, email: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "email": email,
        "iat": now,
        "exp": now + timedelta(minutes=ACCESS_TOKEN_MINUTES),
        "type": "access",
    }
    return jwt.encode(payload, _jwt_secret(), algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    return jwt.decode(token, _jwt_secret(), algorithms=[JWT_ALGORITHM])


# ---------- User serialization ----------

def public_user(doc: dict) -> dict:
    return {
        "id": str(doc["_id"]),
        "email": doc.get("email"),
        "name": doc.get("name") or (doc.get("email") or "").split("@")[0].title(),
        "email_verified": bool(doc.get("email_verified", False)),
        "has_password": bool(doc.get("password_hash")),
        "has_pin": bool(doc.get("pin_hash")),
        "providers": doc.get("providers", []),
        "onboarding_done": bool(doc.get("onboarding_done", False)),
        "has_demo_data": bool(doc.get("has_demo_data", False)),
        "created_at": doc.get("created_at").isoformat() if doc.get("created_at") else None,
    }


# ---------- FastAPI dependencies ----------

def make_get_current_user(db: AsyncIOMotorDatabase):
    async def get_current_user(request: Request) -> dict:
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Not authenticated")
        token = auth_header[7:].strip()
        try:
            payload = decode_token(token)
        except jwt.ExpiredSignatureError:
            raise HTTPException(status_code=401, detail="Session expired. Please sign in again.")
        except jwt.InvalidTokenError:
            raise HTTPException(status_code=401, detail="Invalid session token.")
        if payload.get("type") != "access":
            raise HTTPException(status_code=401, detail="Invalid token type")
        try:
            user = await db.users.find_one({"_id": ObjectId(payload["sub"])})
        except Exception:
            raise HTTPException(status_code=401, detail="Invalid session")
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        return user

    return get_current_user


# ---------- Schemas ----------

class RegisterInput(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    name: Optional[str] = Field(default=None, max_length=80)


class LoginInput(BaseModel):
    email: EmailStr
    password: str


class GoogleLoginInput(BaseModel):
    credential: str


class AppleLoginInput(BaseModel):
    id_token: str
    nonce: Optional[str] = None
    state: Optional[str] = None
    user: Optional[dict] = None


class ForgotPasswordInput(BaseModel):
    email: EmailStr


class ResetPasswordInput(BaseModel):
    token: str
    new_password: str = Field(min_length=8, max_length=128)


class VerifyEmailInput(BaseModel):
    token: str


class PinInput(BaseModel):
    pin: str = Field(min_length=4, max_length=4, pattern=r"^\d{4}$")


class OnboardInput(BaseModel):
    choice: str  # "demo" | "empty"
    name: Optional[str] = None


# ---------- Brute-force helpers ----------

def _as_aware(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


async def check_login_lockout(db, identifier: str):
    rec = await db.login_attempts.find_one({"_id": identifier})
    if not rec:
        return
    if rec.get("count", 0) >= LOGIN_MAX_ATTEMPTS:
        last = _as_aware(rec.get("last_attempt"))
        if last and (datetime.now(timezone.utc) - last).total_seconds() < LOGIN_LOCKOUT_MINUTES * 60:
            raise HTTPException(status_code=429, detail=f"Too many attempts. Try again in {LOGIN_LOCKOUT_MINUTES} minutes.")
        await db.login_attempts.delete_one({"_id": identifier})


async def record_login_failure(db, identifier: str):
    await db.login_attempts.update_one(
        {"_id": identifier},
        {"$inc": {"count": 1}, "$set": {"last_attempt": datetime.now(timezone.utc)}},
        upsert=True,
    )


async def clear_login_failures(db, identifier: str):
    await db.login_attempts.delete_one({"_id": identifier})


# ---------- Router builder ----------

def build_auth_router(db: AsyncIOMotorDatabase) -> APIRouter:
    router = APIRouter(prefix="/auth", tags=["auth"])
    get_current_user = make_get_current_user(db)

    async def _sign_in(user_doc: dict) -> dict:
        token = create_access_token(str(user_doc["_id"]), user_doc.get("email") or "")
        return {"access_token": token, "token_type": "bearer", "user": public_user(user_doc)}

    async def _send_verification_email(user_doc: dict) -> None:
        raw = secrets.token_urlsafe(32)
        digest = hashlib.sha256(raw.encode()).hexdigest()
        await db.email_verification_tokens.insert_one({
            "user_id": user_doc["_id"],
            "token_hash": digest,
            "expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
            "used": False,
            "created_at": datetime.now(timezone.utc),
        })
        frontend = os.environ.get("FRONTEND_URL", "").rstrip("/")
        link = f"{frontend}/verify-email?token={raw}"
        subject, html, text = verify_email_template(user_doc.get("name") or "there", link)
        await send_email(user_doc["email"], subject, html, text)

    @router.post("/register")
    async def register(body: RegisterInput):
        email = body.email.lower().strip()
        existing = await db.users.find_one({"email": email})
        if existing:
            raise HTTPException(status_code=409, detail="An account with this email already exists.")
        now = datetime.now(timezone.utc)
        doc = {
            "email": email,
            "name": (body.name or email.split("@")[0]).strip(),
            "password_hash": hash_secret(body.password),
            "providers": ["email"],
            "email_verified": False,
            "pin_hash": None,
            "onboarding_done": False,
            "has_demo_data": False,
            "created_at": now,
            "updated_at": now,
        }
        result = await db.users.insert_one(doc)
        doc["_id"] = result.inserted_id
        try:
            await _send_verification_email(doc)
        except Exception:
            log.exception("Failed to send verification email")
        return await _sign_in(doc)

    @router.post("/login")
    async def login(body: LoginInput, request: Request):
        email = body.email.lower().strip()
        # Key lockout by email only — behind an ingress, request.client.host is not stable per user
        identifier = f"email:{email}"
        await check_login_lockout(db, identifier)
        user = await db.users.find_one({"email": email})
        if not user or not user.get("password_hash") or not verify_secret(body.password, user["password_hash"]):
            await record_login_failure(db, identifier)
            raise HTTPException(status_code=401, detail="Invalid email or password.")
        await clear_login_failures(db, identifier)
        return await _sign_in(user)

    @router.get("/me")
    async def me(user=Depends(get_current_user)):
        return public_user(user)

    @router.post("/logout")
    async def logout():
        return {"ok": True}

    # ---- Google ----
    @router.post("/google")
    async def google_login(body: GoogleLoginInput):
        client_id = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
        if not client_id:
            raise HTTPException(status_code=503, detail="Google Sign-In is not configured yet. Please add GOOGLE_CLIENT_ID in backend/.env.")
        try:
            from google.oauth2 import id_token as g_id_token
            from google.auth.transport import requests as g_requests
            claims = g_id_token.verify_oauth2_token(body.credential, g_requests.Request(), client_id)
        except Exception as exc:
            log.warning("Google token verification failed: %s", exc)
            raise HTTPException(status_code=401, detail="Invalid Google credential.")
        if claims.get("iss") not in {"accounts.google.com", "https://accounts.google.com"}:
            raise HTTPException(status_code=401, detail="Invalid Google issuer.")
        sub = claims.get("sub")
        if not sub:
            raise HTTPException(status_code=401, detail="Missing subject in Google token.")
        email = (claims.get("email") or "").lower()
        name = claims.get("name") or (email.split("@")[0].title() if email else "Finaura user")
        return await _upsert_and_sign(provider="google", provider_sub=sub, email=email, name=name, email_verified=bool(claims.get("email_verified")))

    # ---- Apple ----
    @router.post("/apple")
    async def apple_login(body: AppleLoginInput):
        client_id = os.environ.get("APPLE_CLIENT_ID", "").strip()
        if not client_id:
            raise HTTPException(status_code=503, detail="Apple Sign-In is not configured yet. Please add APPLE_CLIENT_ID in backend/.env.")
        try:
            from jwt import PyJWKClient
            jwks_client = PyJWKClient("https://appleid.apple.com/auth/keys")
            key = jwks_client.get_signing_key_from_jwt(body.id_token).key
            claims = jwt.decode(
                body.id_token, key, algorithms=["RS256"],
                audience=client_id, issuer="https://appleid.apple.com",
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
        except Exception as exc:
            log.warning("Apple token verification failed: %s", exc)
            raise HTTPException(status_code=401, detail="Invalid Apple credential.")
        sub = claims.get("sub")
        if not sub:
            raise HTTPException(status_code=401, detail="Missing subject in Apple token.")
        # nonce validation
        if body.nonce:
            expected_hash = hashlib.sha256(body.nonce.encode()).hexdigest()
            if claims.get("nonce") not in {body.nonce, expected_hash}:
                raise HTTPException(status_code=401, detail="Apple nonce mismatch.")
        email = (claims.get("email") or "").lower()
        name = None
        if body.user and isinstance(body.user, dict):
            name_obj = body.user.get("name") or {}
            first = name_obj.get("firstName", "") if isinstance(name_obj, dict) else ""
            last = name_obj.get("lastName", "") if isinstance(name_obj, dict) else ""
            name = f"{first} {last}".strip() or None
        if not name:
            name = email.split("@")[0].title() if email else "Finaura user"
        return await _upsert_and_sign(provider="apple", provider_sub=sub, email=email, name=name, email_verified=bool(claims.get("email_verified")))

    async def _upsert_and_sign(provider: str, provider_sub: str, email: str, name: str, email_verified: bool) -> dict:
        provider_field = f"{provider}_sub"
        # Try find by provider sub first
        user = await db.users.find_one({provider_field: provider_sub})
        if not user and email:
            user = await db.users.find_one({"email": email})
        now = datetime.now(timezone.utc)
        if user:
            update = {"$set": {"updated_at": now}, "$addToSet": {"providers": provider}}
            if not user.get(provider_field):
                update["$set"][provider_field] = provider_sub
            if email and not user.get("email"):
                update["$set"]["email"] = email
            if email_verified and not user.get("email_verified"):
                update["$set"]["email_verified"] = True
            await db.users.update_one({"_id": user["_id"]}, update)
            user = await db.users.find_one({"_id": user["_id"]})
        else:
            doc = {
                "email": email or None,
                "name": name,
                "password_hash": None,
                "providers": [provider],
                provider_field: provider_sub,
                "email_verified": email_verified,
                "pin_hash": None,
                "onboarding_done": False,
                "has_demo_data": False,
                "created_at": now,
                "updated_at": now,
            }
            r = await db.users.insert_one(doc)
            doc["_id"] = r.inserted_id
            user = doc
        return await _sign_in(user)

    # ---- Password reset ----
    @router.post("/forgot-password")
    async def forgot_password(body: ForgotPasswordInput):
        email = body.email.lower().strip()
        user = await db.users.find_one({"email": email})
        # Always return generic response; but if user exists, generate token
        if user:
            raw = secrets.token_urlsafe(32)
            digest = hashlib.sha256(raw.encode()).hexdigest()
            await db.password_reset_tokens.insert_one({
                "user_id": user["_id"],
                "token_hash": digest,
                "expires_at": datetime.now(timezone.utc) + timedelta(minutes=15),
                "used": False,
                "created_at": datetime.now(timezone.utc),
            })
            frontend = os.environ.get("FRONTEND_URL", "").rstrip("/")
            link = f"{frontend}/reset-password?token={raw}"
            subject, html, text = reset_password_template(user.get("name") or "there", link)
            await send_email(user["email"], subject, html, text)
        return {"ok": True, "message": "If that email is registered with Finaura, a reset link is on its way."}

    @router.post("/reset-password")
    async def reset_password(body: ResetPasswordInput):
        digest = hashlib.sha256(body.token.encode()).hexdigest()
        rec = await db.password_reset_tokens.find_one_and_update(
            {"token_hash": digest, "used": False, "expires_at": {"$gt": datetime.now(timezone.utc)}},
            {"$set": {"used": True, "used_at": datetime.now(timezone.utc)}},
        )
        if not rec:
            raise HTTPException(status_code=400, detail="This reset link is invalid or has expired.")
        await db.users.update_one(
            {"_id": rec["user_id"]},
            {"$set": {"password_hash": hash_secret(body.new_password), "updated_at": datetime.now(timezone.utc)}},
        )
        user = await db.users.find_one({"_id": rec["user_id"]})
        return await _sign_in(user)

    @router.post("/verify-email")
    async def verify_email(body: VerifyEmailInput):
        digest = hashlib.sha256(body.token.encode()).hexdigest()
        rec = await db.email_verification_tokens.find_one_and_update(
            {"token_hash": digest, "used": False, "expires_at": {"$gt": datetime.now(timezone.utc)}},
            {"$set": {"used": True, "used_at": datetime.now(timezone.utc)}},
        )
        if not rec:
            raise HTTPException(status_code=400, detail="This verification link is invalid or has expired.")
        await db.users.update_one({"_id": rec["user_id"]}, {"$set": {"email_verified": True}})
        return {"ok": True}

    @router.post("/resend-verification")
    async def resend_verification(user=Depends(get_current_user)):
        if user.get("email_verified"):
            return {"ok": True, "already_verified": True}
        if not user.get("email"):
            raise HTTPException(status_code=400, detail="No email on file")
        await _send_verification_email(user)
        return {"ok": True}

    # ---- PIN ----
    @router.post("/set-pin")
    async def set_pin(body: PinInput, user=Depends(get_current_user)):
        await db.users.update_one(
            {"_id": user["_id"]},
            {"$set": {"pin_hash": hash_secret(body.pin), "pin_failures": 0, "updated_at": datetime.now(timezone.utc)}},
        )
        return {"ok": True, "has_pin": True}

    @router.post("/verify-pin")
    async def verify_pin(body: PinInput, user=Depends(get_current_user)):
        pin_hash = user.get("pin_hash")
        if not pin_hash:
            raise HTTPException(status_code=400, detail="No PIN is set for this account.")
        failures = user.get("pin_failures", 0) or 0
        lockout_until = _as_aware(user.get("pin_lockout_until"))
        if lockout_until and lockout_until > datetime.now(timezone.utc):
            raise HTTPException(status_code=429, detail=f"PIN locked. Try again in {PIN_LOCKOUT_MINUTES} minutes.")
        if not verify_secret(body.pin, pin_hash):
            failures += 1
            update: dict = {"pin_failures": failures}
            if failures >= PIN_MAX_ATTEMPTS:
                update["pin_lockout_until"] = datetime.now(timezone.utc) + timedelta(minutes=PIN_LOCKOUT_MINUTES)
                update["pin_failures"] = 0
            await db.users.update_one({"_id": user["_id"]}, {"$set": update})
            remaining = max(0, PIN_MAX_ATTEMPTS - failures)
            raise HTTPException(status_code=401, detail=f"Incorrect PIN. {remaining} attempts left.")
        await db.users.update_one({"_id": user["_id"]}, {"$set": {"pin_failures": 0, "pin_lockout_until": None}})
        return {"ok": True}

    @router.post("/remove-pin")
    async def remove_pin(body: PinInput, user=Depends(get_current_user)):
        # require current PIN
        pin_hash = user.get("pin_hash")
        if not pin_hash or not verify_secret(body.pin, pin_hash):
            raise HTTPException(status_code=401, detail="Incorrect PIN.")
        await db.users.update_one({"_id": user["_id"]}, {"$set": {"pin_hash": None, "pin_failures": 0}})
        return {"ok": True, "has_pin": False}

    @router.post("/onboard")
    async def onboard(body: OnboardInput, user=Depends(get_current_user)):
        update = {"onboarding_done": True, "updated_at": datetime.now(timezone.utc)}
        if body.name:
            update["name"] = body.name.strip()[:80]
        if body.choice == "demo":
            update["has_demo_data"] = True
            # Seed demo data scoped to this user
            from server import DEFAULT_GOALS, TRANSACTIONS
            uid = str(user["_id"])
            # Only seed if empty
            existing_goals = await db.finaura_goals.count_documents({"user_id": uid})
            if existing_goals == 0:
                await db.finaura_goals.insert_many([
                    {**g, "id": str(uuid.uuid4()), "user_id": uid, "created_at": datetime.now(timezone.utc)}
                    for g in DEFAULT_GOALS
                ])
            existing_txns = await db.finaura_transactions.count_documents({"user_id": uid})
            if existing_txns == 0:
                await db.finaura_transactions.insert_many([
                    {**t, "id": str(uuid.uuid4()), "user_id": uid, "created_at": datetime.now(timezone.utc)}
                    for t in TRANSACTIONS
                ])
        await db.users.update_one({"_id": user["_id"]}, {"$set": update})
        user = await db.users.find_one({"_id": user["_id"]})
        return public_user(user)

    @router.delete("/account")
    async def delete_account(user=Depends(get_current_user)):
        uid = str(user["_id"])
        await db.finaura_goals.delete_many({"user_id": uid})
        await db.finaura_transactions.delete_many({"user_id": uid})
        await db.users.delete_one({"_id": user["_id"]})
        return {"deleted": True}

    return router


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    await db.users.create_index("email", unique=True, sparse=True)
    await db.users.create_index("google_sub", sparse=True)
    await db.users.create_index("apple_sub", sparse=True)
    await db.password_reset_tokens.create_index("expires_at", expireAfterSeconds=0)
    await db.email_verification_tokens.create_index("expires_at", expireAfterSeconds=0)
    await db.finaura_goals.create_index("user_id")
    await db.finaura_transactions.create_index("user_id")
