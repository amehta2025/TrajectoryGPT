import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_rate_limits(monkeypatch):
    """Each test starts with empty per-IP rate limits (the TestClient's address is always 'testclient')."""
    from app.safety import ratelimit
    monkeypatch.setattr(ratelimit, "LIMITER", ratelimit.RateLimiter())
    import app.main
    monkeypatch.setattr(app.main, "LIMITER", ratelimit.LIMITER)


@pytest.fixture(autouse=True)
def _tmp_plan_store(tmp_path, monkeypatch):
    """Plans and their run claims are saved to disk (app/agent/session.py); keep each test's in its own folder."""
    from app.agent import session
    monkeypatch.setattr(session, "PLANS_DIR", tmp_path / "plans")
    monkeypatch.setattr(session, "CLAIMS", session.FileClaims())


@pytest.fixture(autouse=True)
def _no_paid_verifier(monkeypatch):
    """Tests never call the real second-AI check (Muse Wonder costs money; it ran on every API test run whenever
    a key was in .env). Runs skip the check; a test that exercises it passes its own fake LLM."""
    monkeypatch.setenv("SKIP_VERIFIER", "1")
    from app.agent import verifier

    def offline(*a, **k):
        raise RuntimeError("tests do not call the real verifier")
    monkeypatch.setattr(verifier, "verifier_llm", offline)
