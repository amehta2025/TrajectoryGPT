"""Push-to-talk voice input: WAV checks, the transcribe request shape, retries and the endpoint (no network)."""
import base64
import io
import json
import wave

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.agent import voice
from app.main import app


def wav(seconds=1.0, rate=24000, channels=1, width=2) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(np.zeros(int(seconds * rate) * channels, dtype=f"<i{width}").tobytes())
    return buf.getvalue()


def test_check_wav_accepts_only_what_the_endpoint_takes():
    assert voice.check_wav(wav(2.0)) == pytest.approx(2.0)
    assert voice.check_wav(wav(1.0, rate=16000)) == pytest.approx(1.0)
    for bad, msg in [(wav(1.0, rate=44100), "16 or 24 kHz"), (wav(1.0, channels=2), "mono"),
                     (wav(1.0, width=4), "16-bit"), (wav(0.1), "Nothing recorded"), (b"RIFFjunk", "Not a WAV")]:
        with pytest.raises(voice.AudioRejected, match=msg):
            voice.check_wav(bad)


def _client(responses, seen):
    it = iter(responses)

    def handler(request: httpx.Request):
        seen.append(request)
        return next(it)
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_request_is_multipart_push_to_talk_with_the_key_in_the_header(monkeypatch):
    monkeypatch.setenv("META_API_KEY", "k")
    monkeypatch.setenv("META_API_BASE_URL", "https://api.meta.ai/v1")
    seen = []
    out = voice.transcribe(wav(2.5), _client([httpx.Response(200, json={"transcript": " Is the drop real? "})], seen))
    r = seen[0]
    assert str(r.url) == "https://api.meta.ai/v1/asr/transcribe" and r.headers["authorization"] == "Bearer k"
    body = r.content
    settings = json.loads(body.split(b'name="request"')[1].split(b"\r\n\r\n", 1)[1].split(b"\r\n--")[0])
    assert settings["mode"] == "PUSH_TO_TALK" and settings["audioEncoding"] == "WAV"
    assert settings["model"] == "muse-voice-transcribe-1.0" and "HSE134" in settings["keywords"]
    assert b'name="audio"; filename="speech.wav"' in body and b"RIFF" in body
    assert out["text"] == "Is the drop real?" and out["seconds"] == 2.5
    assert out["cost_usd"] == pytest.approx(2 / 3600 * 0.18)  # whole seconds only


def test_retries_503_but_never_400(monkeypatch):
    monkeypatch.setenv("META_API_KEY", "k")
    seen = []
    ok = voice.transcribe(wav(), _client([httpx.Response(503), httpx.Response(200, json={"transcript": "hi"})], seen),
                          sleep=lambda s: None)
    assert ok["text"] == "hi" and len(seen) == 2
    seen = []
    with pytest.raises(httpx.HTTPStatusError):
        voice.transcribe(wav(), _client([httpx.Response(400), httpx.Response(200)], seen), sleep=lambda s: None)
    assert len(seen) == 1


def test_endpoint_returns_text_records_cost_and_rejects_bad_audio(monkeypatch, tmp_path):
    from app.safety import budget
    monkeypatch.delenv("DEMO_PASSCODE", raising=False)
    monkeypatch.setattr(budget, "USAGE_DIR", tmp_path)
    monkeypatch.setattr(voice, "transcribe", lambda raw: {"text": "hello", "seconds": 1.0, "model": "m",
                                                          "latency_s": 0.1, "cost_usd": 0.00005})
    c = TestClient(app)
    r = c.post("/transcribe", json={"audio": base64.b64encode(wav()).decode()})
    assert r.status_code == 200 and r.json()["text"] == "hello"
    assert budget.spent_today() == pytest.approx(0.00005)
    assert c.post("/transcribe", json={"audio": "not base64!"}).status_code == 400


def test_endpoint_reports_a_rejected_recording(monkeypatch):
    monkeypatch.delenv("DEMO_PASSCODE", raising=False)
    monkeypatch.setenv("META_API_KEY", "k")
    r = TestClient(app).post("/transcribe", json={"audio": base64.b64encode(wav(0.1)).decode()})
    assert r.status_code == 400 and "hold the key" in r.json()["detail"]


def test_voice_is_off_in_replay_mode(monkeypatch):
    monkeypatch.delenv("DEMO_PASSCODE", raising=False)
    monkeypatch.setenv("APP_MODE", "replay")
    assert TestClient(app).post("/transcribe", json={"audio": "AAAA"}).status_code == 409
    assert TestClient(app).get("/health").json()["voice"] is False
