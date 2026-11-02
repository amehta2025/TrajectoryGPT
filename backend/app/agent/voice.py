"""Push-to-talk voice input (SPEC §9d): a short WAV from the browser -> Muse Voice Transcribe -> text.

The file endpoint (docs_local/meta/speech-to-text.txt): POST {base}/asr/transcribe, multipart with a
`request` part (JSON settings) and an `audio` part (RIFF/WAVE, mono, 16-bit PCM, 16 or 24 kHz). The API
key stays here; the text goes back to the question box for the user to review before anything is sent.
Retries are ours, as for the LLM: 429/500/503 only, honoring Retry-After, 3 tries.
"""
import io
import json
import os
import random
import time
import wave

import httpx

from app.agent.llm import AUDIO_PRICES, MAX_TRIES, RETRYABLE, TIMEOUT_S

RATES = (16000, 24000)
MIN_S, MAX_S = 0.3, 60.0  # a held key: long enough to say something, short enough to stay cheap
MAX_BYTES = 3_000_000  # 60 s at 24 kHz is about 2.9 MB
# Vocabulary biasing: names in this app that a general model may mishear.
KEYWORDS = ["adenylate kinase", "AdK", "LID", "NMP", "CORE", "HSE134", "ASP118", "SER30", "ASP84",
            "contact", "residue", "cutoff", "Q1", "Q2"]


class AudioRejected(ValueError):
    """The upload is not a WAV the endpoint accepts; str(e) is shown to the user."""


def check_wav(raw: bytes) -> float:
    """Return the duration in seconds of a mono 16-bit PCM WAV at 16 or 24 kHz; raise AudioRejected otherwise."""
    if len(raw) > MAX_BYTES:
        raise AudioRejected(f"Recording too long: keep it under {MAX_S:.0f} seconds.")
    try:
        with wave.open(io.BytesIO(raw)) as w:
            channels, width, rate, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
    except (wave.Error, EOFError) as e:
        raise AudioRejected(f"Not a WAV recording ({e}).") from None
    if channels != 1 or width != 2 or rate not in RATES:
        raise AudioRejected(f"Need mono 16-bit audio at 16 or 24 kHz, got {channels} channel(s), "
                            f"{8 * width}-bit, {rate} Hz.")
    seconds = n / rate
    if seconds < MIN_S:
        raise AudioRejected("Nothing recorded: hold the key while you speak.")
    if seconds > MAX_S:
        raise AudioRejected(f"Recording too long: keep it under {MAX_S:.0f} seconds.")
    return seconds


def request_settings(model: str) -> dict:
    return {"model": model, "mode": "PUSH_TO_TALK", "audioEncoding": "WAV",
            "languageBias": ["English"], "keywords": KEYWORDS}


def _retry_after(r: httpx.Response) -> float:
    try:
        return float(r.headers.get("retry-after") or 0)
    except ValueError:
        return 0.0


def transcribe(raw: bytes, client: httpx.Client | None = None, sleep=time.sleep) -> dict:
    """Transcribe one push-to-talk recording. Returns text, seconds, model, latency and estimated $."""
    seconds = check_wav(raw)
    model = os.environ.get("META_ASR_MODEL", "muse-voice-transcribe-1.0")
    base = os.environ.get("META_API_BASE_URL", "https://api.meta.ai/v1").rstrip("/")
    api_key = os.environ.get("META_API_KEY")
    if not api_key:
        raise RuntimeError("META_API_KEY is not set")
    files = {"request": (None, json.dumps(request_settings(model)), "application/json"),
             "audio": ("speech.wav", raw, "audio/wav")}
    client = client or httpx.Client(timeout=TIMEOUT_S)
    t0 = time.perf_counter()
    for attempt in range(MAX_TRIES):
        r = client.post(f"{base}/asr/transcribe", headers={"Authorization": f"Bearer {api_key}"}, files=files)
        if r.status_code not in RETRYABLE or attempt == MAX_TRIES - 1:
            break
        sleep(max(0.5 * 2**attempt + random.random(), _retry_after(r)))
    r.raise_for_status()
    text = (r.json().get("transcript") or "").strip()
    billed_s = int(seconds)  # billed per whole second of audio processed
    return {"text": text, "seconds": round(seconds, 2), "model": model,
            "latency_s": round(time.perf_counter() - t0, 2),
            "cost_usd": billed_s / 3600 * AUDIO_PRICES[model]}
