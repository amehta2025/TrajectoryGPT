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
