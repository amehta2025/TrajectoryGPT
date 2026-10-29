"""Passcode gate and daily budget (SPEC §12): AI endpoints are gated, charts stay public."""
import pytest
from fastapi.testclient import TestClient

from app.agent.llm import CostTracker, Usage
from app.main import app
from app.safety import budget
from app.safety.passcode import passcode_ok


@pytest.fixture(autouse=True)
def tmp_usage(tmp_path, monkeypatch):
    monkeypatch.setattr(budget, "USAGE_DIR", tmp_path)
    return tmp_path


def test_passcode_rules(monkeypatch):
    monkeypatch.delenv("DEMO_PASSCODE", raising=False)
    monkeypatch.delenv("REQUIRE_PASSCODE", raising=False)
    assert passcode_ok(None)                     # local dev: open
    monkeypatch.setenv("REQUIRE_PASSCODE", "1")
    assert not passcode_ok("anything")           # deployed without a secret: fails closed
    monkeypatch.setenv("DEMO_PASSCODE", "s3cret")
    assert passcode_ok("s3cret") and not passcode_ok("wrong") and not passcode_ok(None)


def test_ai_endpoints_need_passcode_charts_do_not(monkeypatch):
    monkeypatch.setenv("DEMO_PASSCODE", "s3cret")
    c = TestClient(app)
    assert c.post("/plan", json={"question": "q"}).status_code == 401
    assert c.post("/runs", json={"plan_id": "x"}).status_code == 401
    assert c.post("/auth/check").status_code == 401
    assert c.post("/auth/check", headers={"X-Demo-Passcode": "s3cret"}).json() == {"ok": True}
    assert c.post("/runs", json={"plan_id": "x"}, headers={"X-Demo-Passcode": "s3cret"}).status_code == 404
    assert c.get("/metadata/adk_transition").status_code == 200


def test_budget_ledger_and_block(monkeypatch):
    monkeypatch.setenv("DAILY_LLM_BUDGET_USD", "0.01")
    t = CostTracker(on_usage=budget.record)
    t.add(Usage("meta", "m", "low", cost_usd=0.004))
    t.add(Usage("meta", "m", "low", cost_usd=None))  # unknown price counts as 0
    assert budget.spent_today() == pytest.approx(0.004)
    budget.check()
    t.add(Usage("meta", "m", "low", cost_usd=0.006))
    with pytest.raises(budget.BudgetReached, match="Daily AI budget reached"):
        budget.check()
    monkeypatch.delenv("DEMO_PASSCODE", raising=False)
    r = TestClient(app).post("/plan", json={"question": "q"})
    assert r.status_code == 503 and "Daily AI budget reached" in r.json()["detail"]
    assert TestClient(app).get("/health").json()["spent_today_usd"] == pytest.approx(0.01)
