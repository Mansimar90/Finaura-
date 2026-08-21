"""Passkey / WebAuthn module for Finaura.

Passkeys act as an alternative to the 4-digit PIN for unlocking the app
after normal email/password (or OAuth) sign-in.  They don't replace the
primary sign-in; they replace the lock screen.
"""
from __future__ import annotations

import base64
import json
import os
import secrets
import logging
from datetime import datetime, timezone, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument
from webauthn import (
    generate_registration_options,
    generate_authentication_options,
    verify_registration_response,
    verify_authentication_response,
    options_to_json,
)
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    ResidentKeyRequirement,
    UserVerificationRequirement,
    PublicKeyCredentialDescriptor,
    AuthenticatorTransport,
)

log = logging.getLogger("finaura.passkey")


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64u(value: str) -> bytes:
    pad = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + pad)


def _now():
    return datetime.now(timezone.utc)


def _rp_id() -> str:
    return os.environ.get("WEBAUTHN_RP_ID", "").strip() or "localhost"


def _rp_name() -> str:
    return os.environ.get("WEBAUTHN_RP_NAME", "Finaura")


def _origin() -> str:
    return os.environ.get("FRONTEND_URL", "").rstrip("/") or f"https://{_rp_id()}"


def build_passkey_router(db: AsyncIOMotorDatabase, get_current_user):
    router = APIRouter(prefix="/auth/passkey", tags=["passkey"])

    @router.get("/list")
    async def list_credentials(user=Depends(get_current_user)):
        uid = str(user["_id"])
        rows = await db.passkey_credentials.find({"user_id": uid}).to_list(20)
        return {
            "credentials": [
                {
                    "id": row["credential_id"][:12],
                    "created_at": row.get("created_at").isoformat() if row.get("created_at") else None,
                    "label": row.get("label") or "Passkey",
                    "transports": row.get("transports", []),
                }
                for row in rows
            ]
        }

    @router.post("/register/begin")
    async def register_begin(user=Depends(get_current_user)):
        uid = str(user["_id"])
        existing = await db.passkey_credentials.find({"user_id": uid}).to_list(20)
        challenge = secrets.token_bytes(32)
        exclude = [
            PublicKeyCredentialDescriptor(
                id=_unb64u(row["credential_id"]),
                transports=[
                    AuthenticatorTransport(t)
                    for t in row.get("transports", [])
                    if t in {x.value for x in AuthenticatorTransport}
                ] or None,
            )
            for row in existing
        ]
        options = generate_registration_options(
            rp_id=_rp_id(),
            rp_name=_rp_name(),
            user_id=uid.encode("utf-8"),
            user_name=user.get("email") or f"user-{uid}",
            user_display_name=user.get("name") or user.get("email") or "Finaura user",
            challenge=challenge,
            exclude_credentials=exclude,
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.PREFERRED,
                user_verification=UserVerificationRequirement.PREFERRED,
            ),
            timeout=60000,
        )
        await db.webauthn_challenges.insert_one({
            "user_id": uid,
            "purpose": "register",
            "challenge": _b64u(challenge),
            "expires_at": _now() + timedelta(minutes=5),
            "used": False,
        })
        return json.loads(options_to_json(options))

    @router.post("/register/complete")
    async def register_complete(payload: dict[str, Any], user=Depends(get_current_user)):
        uid = str(user["_id"])
        pending = await db.webauthn_challenges.find_one_and_update(
            {"user_id": uid, "purpose": "register", "used": False, "expires_at": {"$gt": _now()}},
            {"$set": {"used": True, "used_at": _now()}},
            sort=[("_id", -1)],
            return_document=ReturnDocument.BEFORE,
        )
        if not pending:
            raise HTTPException(status_code=400, detail="Passkey registration challenge expired or already used.")
        try:
            verification = verify_registration_response(
                credential=payload,
                expected_challenge=_unb64u(pending["challenge"]),
                expected_rp_id=_rp_id(),
                expected_origin=_origin(),
                require_user_verification=False,
            )
        except Exception as exc:
            log.warning("Passkey registration verify failed: %s", exc)
            raise HTTPException(status_code=400, detail=f"Passkey registration could not be verified: {exc}") from exc
        credential_id_b64 = _b64u(verification.credential_id)
        transports = payload.get("response", {}).get("transports", []) if isinstance(payload.get("response"), dict) else []
        await db.passkey_credentials.update_one(
            {"user_id": uid, "credential_id": credential_id_b64},
            {"$set": {
                "user_id": uid,
                "credential_id": credential_id_b64,
                "public_key": _b64u(verification.credential_public_key),
                "sign_count": verification.sign_count,
                "transports": transports,
                "label": payload.get("label") or "Passkey",
                "created_at": _now(),
            }},
            upsert=True,
        )
        await db.users.update_one({"_id": user["_id"]}, {"$set": {"has_passkey": True}})
        return {"ok": True, "credential_id": credential_id_b64[:12]}

    @router.post("/authenticate/begin")
    async def authenticate_begin(user=Depends(get_current_user)):
        uid = str(user["_id"])
        rows = await db.passkey_credentials.find({"user_id": uid}).to_list(20)
        if not rows:
            raise HTTPException(status_code=404, detail="No passkey is registered for this account.")
        challenge = secrets.token_bytes(32)
        allow = [
            PublicKeyCredentialDescriptor(
                id=_unb64u(row["credential_id"]),
                transports=[
                    AuthenticatorTransport(t)
                    for t in row.get("transports", [])
                    if t in {x.value for x in AuthenticatorTransport}
                ] or None,
            )
            for row in rows
        ]
        options = generate_authentication_options(
            rp_id=_rp_id(),
            challenge=challenge,
            allow_credentials=allow,
            user_verification=UserVerificationRequirement.PREFERRED,
            timeout=60000,
        )
        await db.webauthn_challenges.insert_one({
            "user_id": uid,
            "purpose": "authenticate",
            "challenge": _b64u(challenge),
            "expires_at": _now() + timedelta(minutes=5),
            "used": False,
        })
        return json.loads(options_to_json(options))

    @router.post("/authenticate/complete")
    async def authenticate_complete(payload: dict[str, Any], user=Depends(get_current_user)):
        uid = str(user["_id"])
        credential_id = payload.get("id")
        if not credential_id:
            raise HTTPException(status_code=400, detail="Missing credential id.")
        pending = await db.webauthn_challenges.find_one_and_update(
            {"user_id": uid, "purpose": "authenticate", "used": False, "expires_at": {"$gt": _now()}},
            {"$set": {"used": True, "used_at": _now()}},
            sort=[("_id", -1)],
            return_document=ReturnDocument.BEFORE,
        )
        row = await db.passkey_credentials.find_one({"user_id": uid, "credential_id": credential_id})
        if not pending or not row:
            raise HTTPException(status_code=400, detail="Invalid or expired passkey request.")
        try:
            verification = verify_authentication_response(
                credential=payload,
                expected_challenge=_unb64u(pending["challenge"]),
                expected_rp_id=_rp_id(),
                expected_origin=_origin(),
                credential_public_key=_unb64u(row["public_key"]),
                credential_current_sign_count=row.get("sign_count", 0),
                require_user_verification=False,
            )
        except Exception as exc:
            log.warning("Passkey auth verify failed: %s", exc)
            raise HTTPException(status_code=401, detail="Passkey verification failed.") from exc
        await db.passkey_credentials.update_one(
            {"_id": row["_id"]}, {"$set": {"sign_count": verification.new_sign_count, "last_used_at": _now()}}
        )
        return {"ok": True}

    @router.delete("/{credential_prefix}")
    async def remove_passkey(credential_prefix: str, user=Depends(get_current_user)):
        uid = str(user["_id"])
        rows = await db.passkey_credentials.find({"user_id": uid}).to_list(20)
        target = next((r for r in rows if r["credential_id"].startswith(credential_prefix)), None)
        if not target:
            raise HTTPException(status_code=404, detail="Passkey not found.")
        await db.passkey_credentials.delete_one({"_id": target["_id"]})
        remaining = await db.passkey_credentials.count_documents({"user_id": uid})
        await db.users.update_one({"_id": user["_id"]}, {"$set": {"has_passkey": remaining > 0}})
        return {"ok": True, "remaining": remaining}

    return router


async def ensure_passkey_indexes(db: AsyncIOMotorDatabase) -> None:
    await db.passkey_credentials.create_index([("user_id", 1), ("credential_id", 1)], unique=True)
    await db.passkey_credentials.create_index("credential_id", unique=True)
    await db.webauthn_challenges.create_index("expires_at", expireAfterSeconds=0)
