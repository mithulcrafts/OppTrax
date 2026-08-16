import os
import time
import base64
import requests
import fitz  # PyMuPDF
from config import WHATSAPP_TOKEN, PHONE_NUMBER_ID, SARVAM_KEY, sarvam_client
from services.whatsapp_api import upload_whatsapp_media, send_whatsapp_audio

# =====================================================================
#  VOICE ENGINE (Sarvam TTS -> Meta Media Upload -> WhatsApp Audio)
# =====================================================================

def generate_sarvam_audio(text_to_speak: str) -> str | None:
    print(f"[TTS] Synthesizing voice for: {text_to_speak[:80]}...", flush=True)
    url = "https://api.sarvam.ai/text-to-speech"
    payload = {
        "inputs": [text_to_speak[:500]],
        "target_language_code": "en-IN",
        "speaker": "anushka",
        "model": "bulbul:v2",
        "output_audio_codec": "mp3"
    }
    headers = {"api-subscription-key": SARVAM_KEY, "Content-Type": "application/json"}
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=15)
        if response.status_code == 200:
            audio_base64 = response.json()["audios"][0]
            file_path = f"opptrax_alert_{int(time.time())}.mp3"
            with open(file_path, "wb") as f:
                f.write(base64.b64decode(audio_base64))
            print(f"[TTS] Audio saved to {file_path}", flush=True)
            return file_path
    except Exception as e:
        print(f"[TTS] Request failed: {e}", flush=True)
    return None

def deliver_voice_alert(to_phone: str, alert_text: str):
    audio_path = generate_sarvam_audio(alert_text)
    if not audio_path: return
    try:
        media_id = upload_whatsapp_media(audio_path)
        if media_id:
            send_whatsapp_audio(to_phone, media_id)
    finally:
        if os.path.exists(audio_path):
            os.remove(audio_path)

# =====================================================================
#  INBOUND MEDIA (Meta Download -> Sarvam STT / PyMuPDF)
# =====================================================================

def download_whatsapp_media(media_id: str, ext: str = "ogg"):
    url = f"https://graph.facebook.com/v20.0/{media_id}"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}
    try:
        response = requests.get(url, headers=headers, timeout=15)
        if response.status_code != 200:
            return None
        media_url = response.json().get("url")
        file_response = requests.get(media_url, headers=headers, timeout=15)
        if file_response.status_code == 200:
            file_path = f"temp_media_{media_id}.{ext}"
            with open(file_path, "wb") as f:
                f.write(file_response.content)
            return file_path
    except Exception as e:
        print(f"Media Download Error: {e}")
    return None

def transcribe_voice_note(file_path: str):
    try:
        with open(file_path, "rb") as audio_file:
            response = sarvam_client.speech_to_text.transcribe(
                file=audio_file,
                model="saaras:v3",
                mode="translate",
                language_code="hi-IN"
            )
            return response.transcript
    except Exception as e:
        print(f"Sarvam Failed: {e}")
        return None

def extract_text_from_pdf(file_path: str) -> str | None:
    try:
        doc = fitz.open(file_path)
        text = ""
        for page in doc:
            text += page.get_text()
        return text
    except Exception as e:
        print(f"PDF Parse Error: {e}")
        return None
