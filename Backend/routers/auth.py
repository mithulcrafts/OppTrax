import random
import time
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from database import db, users_collection
from services.whatsapp_api import send_otp_message, send_whatsapp_message

router = APIRouter(prefix="/api/auth", tags=["auth"])

otp_verifications = db["otp_verifications"]

class SendOTPRequest(BaseModel):
    phone: str

class VerifyOTPRequest(BaseModel):
    phone: str
    otp: str

def sanitize_phone_number(phone: str) -> str:
    # Remove all non-numeric characters
    cleaned = "".join(c for c in phone if c.isdigit())
    if len(cleaned) == 10:
        # Default to Indian country code (+91) if 10 digits
        cleaned = "91" + cleaned
    return cleaned

@router.post("/send-otp")
async def send_otp(req: SendOTPRequest):
    phone = req.phone.strip()
    if not phone:
        raise HTTPException(status_code=400, detail="Phone number is required.")
        
    sanitized_phone = sanitize_phone_number(phone)
    if len(sanitized_phone) < 10 or len(sanitized_phone) > 15:
        raise HTTPException(status_code=400, detail="Invalid phone number format. Please provide country code.")
        
    # Generate 6-digit OTP
    otp = f"{random.randint(100000, 999999)}"
    
    # Expiration in 5 minutes
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
    
    # Store in DB (upsert based on phone number to prevent duplicates)
    await otp_verifications.update_one(
        {"whatsapp_phone": sanitized_phone},
        {
            "$set": {
                "otp": otp,
                "expires_at": expires_at,
                "created_at": datetime.now(timezone.utc)
            }
        },
        upsert=True
    )
    
    # Log the OTP to the console so developers can test without valid Meta API keys configured
    print(f"\n========================================\n[OTP DEBUG] Phone: {sanitized_phone} | Code: {otp}\n========================================\n", flush=True)
    
    # Send via WhatsApp API
    try:
        send_otp_message(sanitized_phone, otp)
        return {"status": "success", "message": "OTP sent successfully."}
    except Exception as e:
        print(f"[OTP ERROR] Failed to send OTP to {sanitized_phone} via WhatsApp: {e}", flush=True)
        error_str = str(e)
        if "131030" in error_str:
            return {
                "status": "sandbox_error",
                "message": "Meta Sandbox Restriction: This phone number is not registered in the Meta Developer Console's allowed recipient list.",
                "detail": "allowed_list_error"
            }
        else:
            return {
                "status": "sandbox_error",
                "message": f"WhatsApp API Error: {error_str}",
                "detail": "api_error"
            }

@router.post("/verify-otp")
async def verify_otp(req: VerifyOTPRequest):
    phone = req.phone.strip()
    otp_code = req.otp.strip()
    
    if not phone or not otp_code:
        raise HTTPException(status_code=400, detail="Phone number and OTP are required.")
        
    sanitized_phone = sanitize_phone_number(phone)
    
    # Find active verification record
    record = await otp_verifications.find_one({"whatsapp_phone": sanitized_phone})
    if not record:
        raise HTTPException(status_code=400, detail="No verification pending for this phone number.")
        
    # Check expiry
    expires_at = record.get("expires_at")
    # Make sure expires_at is timezone-aware
    if expires_at and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
        
    if datetime.now(timezone.utc) > expires_at:
        await otp_verifications.delete_one({"whatsapp_phone": sanitized_phone})
        raise HTTPException(status_code=400, detail="OTP has expired. Please request a new one.")
        
    # Check OTP correctness
    if record.get("otp") != otp_code:
        raise HTTPException(status_code=400, detail="Invalid OTP code.")
        
    # OTP is valid! Remove verification record
    await otp_verifications.delete_one({"whatsapp_phone": sanitized_phone})
    
    # Register/Onboard user in database if they don't exist
    user = await users_collection.find_one({"whatsapp_phone": sanitized_phone})
    if not user:
        await users_collection.insert_one({
            "whatsapp_phone": sanitized_phone,
            "profile_json": None,
            "onboarding_state": "COMPLETED",
            "chat_context_id": None
        })
        
        # Send greeting message
        welcome_text = (
            "*Welcome to OppTrax Universal Agent!*\n\n"
            "Your account has been successfully verified and activated from the website! 🎉\n\n"
            "I am your autonomous web scouting companion. You can use these commands anytime:\n"
            "- *list* - View all active scouts\n"
            "- *stop <Task ID>* - Stop a scout\n"
            "- *help* - Show the command menu\n\n"
            "*Try asking me to track something:*\n"
            "- \"Notify me as soon as a new AI startup funding seed round gets announced.\"\n"
            "- \"Track new VC seed rounds announced in SF.\""
        )
        try:
            send_whatsapp_message(sanitized_phone, welcome_text)
        except Exception as e:
            print(f"[OTP ERROR] Failed to send welcome message: {e}", flush=True)
    else:
        # User already exists, update state to COMPLETED if not already
        if user.get("onboarding_state") != "COMPLETED":
            await users_collection.update_one(
                {"whatsapp_phone": sanitized_phone},
                {"$set": {"onboarding_state": "COMPLETED"}}
            )
        try:
            send_whatsapp_message(sanitized_phone, "*Verification Successful!* Welcome back to OppTrax. Your tracker is active.")
        except Exception as e:
            print(f"[OTP ERROR] Failed to send success verification message: {e}", flush=True)

    return {"status": "success", "message": "Verification successful."}
