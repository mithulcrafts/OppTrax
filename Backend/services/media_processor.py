import asyncio
import base64
import glob
import json
import os
import shutil
import subprocess
import tempfile
import time
from typing import Any

import fitz  # PyMuPDF
import requests
from google.genai import types

from config import (
    GATEKEEPER_MODEL,
    SARVAM_KEY,
    WHATSAPP_API_VERSION,
    WHATSAPP_TOKEN,
    gemini_client,
    generate_content_with_retry,
    sarvam_client,
)
from services.whatsapp_api import send_whatsapp_audio, upload_whatsapp_media


def find_ffmpeg() -> str | None:
    env_path = os.getenv("FFMPEG_PATH")
    if env_path and os.path.isfile(env_path):
        return env_path
    found = shutil.which("ffmpeg")
    if found:
        return found
    win_candidate = r"E:\ffmpeg\ffmpeg-7.1.1-essentials_build\bin\ffmpeg.exe"
    if os.path.isfile(win_candidate):
        return win_candidate
    return None


def _generate_sarvam_audio_sync(text_to_speak: str, language_code: str = "en-IN") -> str | None:
    if not SARVAM_KEY:
        return None
    print(f"[TTS] Synthesizing voice ({language_code}) for: {text_to_speak[:80]}...", flush=True)
    url = "https://api.sarvam.ai/text-to-speech"
    payload = {
        "inputs": [text_to_speak[:500]],
        "target_language_code": language_code,
        "speaker": "anushka",
        "model": "bulbul:v2",
        "output_audio_codec": "mp3",
    }
    headers = {"api-subscription-key": SARVAM_KEY, "Content-Type": "application/json"}
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=15)
        if response.status_code == 200:
            audio_base64 = response.json()["audios"][0]
            file_path = os.path.join(tempfile.gettempdir(), f"opptrax_alert_{int(time.time() * 1000)}.mp3")
            with open(file_path, "wb") as f:
                f.write(base64.b64decode(audio_base64))
            print(f"[TTS] Audio saved to {file_path}", flush=True)
            return file_path
        print(f"[TTS] Sarvam HTTP {response.status_code}: {response.text}", flush=True)
    except Exception as exc:
        print(f"[TTS] Request failed: {exc}", flush=True)
    return None


async def generate_sarvam_audio(text_to_speak: str, language_code: str = "en-IN") -> str | None:
    return await asyncio.to_thread(_generate_sarvam_audio_sync, text_to_speak, language_code)


async def deliver_voice_alert(to_phone: str, alert_text: str, preferred_language: str = "en") -> bool:
    lang_code = "hi-IN" if preferred_language == "hi" else "en-IN"
    audio_path = await generate_sarvam_audio(alert_text, language_code=lang_code)
    if not audio_path:
        return False
    try:
        media_id = await upload_whatsapp_media(audio_path)
        if media_id:
            await send_whatsapp_audio(to_phone, media_id)
            return True
    finally:
        if os.path.exists(audio_path):
            os.remove(audio_path)
    return False


def _download_whatsapp_media_sync(media_id: str, ext: str = "ogg") -> str | None:
    if not WHATSAPP_TOKEN:
        return None
    url = f"https://graph.facebook.com/{WHATSAPP_API_VERSION}/{media_id}"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}
    try:
        response = requests.get(url, headers=headers, timeout=15)
        if response.status_code != 200:
            print(f"[MEDIA] Lookup failed {response.status_code}: {response.text}", flush=True)
            return None
        media_url = response.json().get("url")
        if not media_url:
            return None
        file_response = requests.get(media_url, headers=headers, timeout=20)
        if file_response.status_code == 200:
            safe_ext = "".join(c for c in ext if c.isalnum()) or "bin"
            file_path = os.path.join(tempfile.gettempdir(), f"opptrax_media_{media_id}.{safe_ext}")
            with open(file_path, "wb") as f:
                f.write(file_response.content)
            return file_path
    except Exception as exc:
        print(f"[MEDIA] Download error: {exc}", flush=True)
    return None


async def download_whatsapp_media(media_id: str, ext: str = "ogg") -> str | None:
    return await asyncio.to_thread(_download_whatsapp_media_sync, media_id, ext)


def _transcribe_single_file(file_path: str) -> tuple[str | None, str | None]:
    with open(file_path, "rb") as audio_file:
        response = sarvam_client.speech_to_text.transcribe(
            file=audio_file,
            model="saaras:v3",
            mode="transcribe",
        )
        transcript = getattr(response, "transcript", None)
        lang_code = getattr(response, "language_code", None)
        return transcript, lang_code


def _transcribe_chunks(chunks: list[str]) -> dict[str, Any] | None:
    full_transcript: list[str] = []
    detected_lang: str | None = None
    for chunk_file in chunks:
        try:
            transcript, lang_code = _transcribe_single_file(chunk_file)
            if transcript:
                full_transcript.append(transcript)
            if lang_code and not detected_lang:
                detected_lang = lang_code
        except Exception as exc:
            print(f"[STT] Sarvam chunk failed ({chunk_file}): {exc}", flush=True)
        finally:
            if os.path.exists(chunk_file):
                os.remove(chunk_file)
    joined = " ".join(full_transcript).strip()
    return {"transcript": joined, "language_code": detected_lang} if joined else None


def _transcribe_voice_note_sync(file_path: str) -> dict[str, Any] | None:
    if not sarvam_client:
        return None
    try:
        ffmpeg_exe = find_ffmpeg()
        if not ffmpeg_exe:
            transcript, lang_code = _transcribe_single_file(file_path)
            return {"transcript": transcript.strip(), "language_code": lang_code} if transcript else None

        tmp_dir = tempfile.gettempdir()
        base_name = os.path.basename(file_path)
        chunk_pattern = os.path.join(tmp_dir, f"opptrax_chunk_%03d_{base_name}.wav")
        search_pattern = os.path.join(tmp_dir, f"opptrax_chunk_*_{base_name}.wav")

        cmd = [ffmpeg_exe, "-y", "-i", file_path, "-f", "segment", "-segment_time", "29", chunk_pattern]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        chunks = sorted(glob.glob(search_pattern))
        if not chunks:
            transcript, lang_code = _transcribe_single_file(file_path)
            return {"transcript": transcript.strip(), "language_code": lang_code} if transcript else None
        return _transcribe_chunks(chunks)
    except Exception as exc:
        print(f"[STT] Sarvam processing failed: {exc}", flush=True)
        return None


async def transcribe_voice_note(file_path: str) -> dict[str, Any] | None:
    return await asyncio.to_thread(_transcribe_voice_note_sync, file_path)


def extract_text_from_pdf(file_path: str) -> str | None:
    try:
        with fitz.open(file_path) as doc:
            text_parts = [page.get_text() for page in doc]
        joined = "\n".join(text_parts).strip()
        return joined or None
    except Exception as exc:
        print(f"[PDF] Parse error: {exc}", flush=True)
        return None


async def extract_opportunity_from_image(
    file_path: str,
    mime_type: str = "image/jpeg",
    caption: str = "",
) -> dict[str, Any] | None:
    """Uses Gemini Vision to parse opportunity posters/screenshots into bilingual structured fields."""
    if not gemini_client:
        return None
    try:
        with open(file_path, "rb") as img_file:
            img_bytes = img_file.read()

        prompt = f"""Extract full opportunity details from this poster/screenshot (and user caption: "{caption}").
Return a STRICT JSON object:
{{
  "title": "Exact canonical opportunity title in English",
  "title_hi": "Natural Hindi translation of the title",
  "organization": "Host company / university / organizer",
  "category": "internship | job | hackathon | scholarship | conference | general",
  "location": "Remote / City / Country or Not specified",
  "stipend_or_reward": "Salary, stipend, prize pool, or Not specified",
  "eligibility": "Who can apply",
  "application_steps": "How to apply",
  "deadline_iso": "ISO 8601 UTC string e.g. 2026-12-31T23:59:59Z or null",
  "url": "Application or info URL visible in image/caption, or null",
  "summary_en": "Clear structured bullet summary in English without emojis",
  "summary_hi": "Clear structured bullet summary in Hindi Devanagari without emojis"
}}"""
        res = await generate_content_with_retry(
            model=GATEKEEPER_MODEL,
            contents=[
                types.Part.from_bytes(data=img_bytes, mime_type=mime_type),
                prompt,
            ],
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )
        return json.loads(res.text.strip())
    except Exception as exc:
        print(f"[VISION] Poster extraction failed: {exc}", flush=True)
        return None
