import hashlib
import hmac
import random
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from config import AUTH_SECRET, WHATSAPP_BOT_NUMBER, integration_status
from database import otp_verifications_collection, users_collection
from services.language import normalize_language
from services.whatsapp_api import (
    fetch_meta_setup_diagnostics,
    get_whatsapp_connect_link,
    send_bilingual_welcome_and_onboarding,
    send_otp_message,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class SendOTPRequest(BaseModel):
    phone: str


class VerifyOTPRequest(BaseModel):
    phone: str
    otp: str
    preferred_language: str | None = "both"


class DirectConnectRequest(BaseModel):
    phone: str
    preferred_language: str | None = "both"


def sanitize_phone_number(phone: str) -> str:
    cleaned = "".join(c for c in phone if c.isdigit())
    if len(cleaned) == 10:
        cleaned = "91" + cleaned
    return cleaned


def create_session_token(phone: str) -> str:
    sig = hmac.new(AUTH_SECRET.encode("utf-8"), phone.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{phone}.{sig}"


def verify_session_token(token: str | None) -> str | None:
    if not token or "." not in token:
        return None
    phone, sig = token.split(".", 1)
    expected = hmac.new(AUTH_SECRET.encode("utf-8"), phone.encode("utf-8"), hashlib.sha256).hexdigest()
    if hmac.compare_digest(sig, expected):
        return phone
    return None


async def resolve_authenticated_phone(
    x_session_token: str | None = Header(default=None),
    x_whatsapp_phone: str | None = Header(default=None),
    phone: str | None = None,
) -> str:
    if x_session_token:
        verified = verify_session_token(x_session_token)
        if not verified:
            raise HTTPException(status_code=401, detail="Invalid session token.")
        return verified

    raw_phone = x_whatsapp_phone or phone
    if raw_phone:
        cleaned = sanitize_phone_number(raw_phone)
        user = await users_collection.find_one({"whatsapp_phone": cleaned})
        if user:
            return cleaned
    raise HTTPException(status_code=401, detail="WhatsApp authentication required. Verify your number first.")


def _serialize_user(user: dict[str, Any] | None, phone: str) -> dict[str, Any]:
    u = user or {}
    onboarding_status = u.get("onboarding_status") or ("COMPLETED" if u.get("profile_json") else "WELCOME_PENDING")
    return {
        "whatsapp_phone": phone,
        "preferred_language": u.get("preferred_language", "both"),
        "digest_mode": u.get("digest_mode", "instant"),
        "onboarding_status": onboarding_status,
        "onboarding_state": onboarding_status,
        "profile_json": u.get("profile_json"),
        "learned_preferences": u.get("learned_preferences", {}),
        "saved_opportunities": u.get("saved_opportunities", []),
        "active_qa_opp_id": u.get("active_qa_opp_id"),
        "whatsapp_verified": bool(u.get("whatsapp_verified", False)),
    }


@router.get("/whatsapp-config")
async def whatsapp_connection_config(probe: bool = False):
    status = integration_status()
    payload = {
        "status": "ok",
        "bot_number": WHATSAPP_BOT_NUMBER,
        "whatsapp_deep_link": get_whatsapp_connect_link(),
        "providers": status,
        "sandbox_mode": not (status["whatsapp_meta"] or status["whatsapp_twilio"]),
    }
    if probe:
        payload["meta_probe"] = await fetch_meta_setup_diagnostics()
    return payload


@router.post("/send-otp")
async def send_otp(req: SendOTPRequest):
    phone = req.phone.strip()
    if not phone:
        raise HTTPException(status_code=400, detail="Phone number is required.")

    sanitized_phone = sanitize_phone_number(phone)
    if len(sanitized_phone) < 10 or len(sanitized_phone) > 15:
        raise HTTPException(
            status_code=400,
            detail="Invalid phone number format. Please provide country code (e.g. +91 9876543210).",
        )

    otp = f"{random.randint(100000, 999999)}"
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)

    await otp_verifications_collection.update_one(
        {"whatsapp_phone": sanitized_phone},
        {
            "$set": {
                "otp": otp,
                "expires_at": expires_at.isoformat(),
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        },
        upsert=True,
    )

    print(
        f"\n========================================\n"
        f"[OTP DEBUG] Phone: {sanitized_phone} | Code: {otp}\n"
        f"========================================\n",
        flush=True,
    )

    try:
        delivery = await send_otp_message(sanitized_phone, otp)
        if delivery.get("status") == "sandbox_queued":
            return {
                "status": "sandbox_mode",
                "message": (
                    "WhatsApp Cloud API credentials are not configured; "
                    "OTP logged in server console & Live WhatsApp Bridge."
                ),
                "detail": "sandbox_queued",
                "phone": sanitized_phone,
                "whatsapp_deep_link": get_whatsapp_connect_link(f"VERIFY {otp}"),
            }
        return {
            "status": "success",
            "message": "OTP sent successfully to your WhatsApp!",
            "phone": sanitized_phone,
            "whatsapp_deep_link": get_whatsapp_connect_link(),
        }
    except Exception as exc:
        error_str = str(exc)
        print(f"[OTP ERROR] Failed to send OTP to {sanitized_phone} via WhatsApp: {error_str}", flush=True)
        if "131030" in error_str:
            return {
                "status": "sandbox_error",
                "message": (
                    "Meta Sandbox Restriction: This phone number is not registered "
                    "in the Meta Developer Console's allowed recipient list. "
                    f"Use OTP: {otp} to enter manually."
                ),
                "detail": "allowed_list_error",
                "phone": sanitized_phone,
                "dev_otp": otp,
                "whatsapp_deep_link": get_whatsapp_connect_link(f"Hi OppTrax! My code is {otp}"),
            }
        return {
            "status": "sandbox_error",
            "message": (
                "WhatsApp Cloud API Error: Meta Phone Number ID is invalid or missing permissions. "
                f"For local demo testing, your verification code is: {otp}"
            ),
            "detail": "api_error",
            "phone": sanitized_phone,
            "dev_otp": otp,
            "whatsapp_deep_link": get_whatsapp_connect_link(f"VERIFY {otp}"),
        }


def _parse_expiry(raw_exp: Any) -> datetime | None:
    if isinstance(raw_exp, datetime):
        return raw_exp if raw_exp.tzinfo else raw_exp.replace(tzinfo=timezone.utc)
    if isinstance(raw_exp, str):
        parsed = datetime.fromisoformat(raw_exp.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


@router.post("/verify-otp")
async def verify_otp(req: VerifyOTPRequest):
    phone = req.phone.strip()
    otp_code = req.otp.strip()
    pref_lang = normalize_language(req.preferred_language, default="both")

    if not phone or not otp_code:
        raise HTTPException(status_code=400, detail="Phone number and OTP are required.")

    sanitized_phone = sanitize_phone_number(phone)
    record = await otp_verifications_collection.find_one({"whatsapp_phone": sanitized_phone})
    if not record:
        raise HTTPException(status_code=400, detail="No verification pending for this phone number.")

    expires_at = _parse_expiry(record.get("expires_at"))
    if expires_at and datetime.now(timezone.utc) > expires_at:
        await otp_verifications_collection.delete_one({"whatsapp_phone": sanitized_phone})
        raise HTTPException(status_code=400, detail="OTP has expired. Please request a new one.")

    if record.get("otp") != otp_code:
        raise HTTPException(status_code=400, detail="Invalid OTP code.")

    await otp_verifications_collection.delete_one({"whatsapp_phone": sanitized_phone})

    user = await users_collection.find_one({"whatsapp_phone": sanitized_phone})
    if not user:
        await users_collection.insert_one(
            {
                "whatsapp_phone": sanitized_phone,
                "profile_json": None,
                "preferred_language": pref_lang,
                "digest_mode": "instant",
                "onboarding_status": "WELCOME_PENDING",
                "whatsapp_verified": True,
                "learned_preferences": {"liked_keywords": [], "disliked_keywords": [], "liked_categories": []},
                "saved_opportunities": [],
                "qa_history": {},
                "created_at": time.time(),
            }
        )
    else:
        next_status = user.get("onboarding_status") or ("COMPLETED" if user.get("profile_json") else "WELCOME_PENDING")
        await users_collection.update_one(
            {"whatsapp_phone": sanitized_phone},
            {
                "$set": {
                    "onboarding_status": next_status,
                    "preferred_language": pref_lang,
                    "whatsapp_verified": True,
                }
            },
        )

    try:
        await send_bilingual_welcome_and_onboarding(sanitized_phone)
    except Exception as exc:
        print(f"[OTP ERROR] Failed to send WhatsApp welcome card: {exc}", flush=True)

    fresh_user = await users_collection.find_one({"whatsapp_phone": sanitized_phone})
    token = create_session_token(sanitized_phone)
    return {
        "status": "success",
        "message": "Verification successful. WhatsApp connected!",
        "session_token": token,
        "user": _serialize_user(fresh_user, sanitized_phone),
        "whatsapp_deep_link": get_whatsapp_connect_link(),
    }


@router.get("/session")
async def get_session(x_session_token: str | None = Header(default=None)):
    phone = verify_session_token(x_session_token)
    if not phone:
        raise HTTPException(status_code=401, detail="Invalid or expired session.")
    user = await users_collection.find_one({"whatsapp_phone": phone})
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    return {
        "status": "ok",
        "user": _serialize_user(user, phone),
        "whatsapp_deep_link": get_whatsapp_connect_link(),
    }
