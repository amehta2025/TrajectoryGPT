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
    # Budget used up -> replay mode (SPEC §12): no model call; an unrecorded question lists the recorded runs.
    r = TestClient(app).post("/plan", json={"question": "q"})
    assert r.status_code == 409 and r.json()["detail"]["replay"] is True
    h = TestClient(app).get("/health").json()
    assert h["spent_today_usd"] == pytest.approx(0.01) and h["mode"] == "replay" and "budget" in h["mode_reason"]


# ---------- rate limit on the real client IP (SPEC §12) ----------

from app.safety import ratelimit  # noqa: E402


def test_client_ip_ignores_forwarding_headers_by_default(monkeypatch):
    monkeypatch.delenv("TRUSTED_PROXY_HOPS", raising=False)
    assert ratelimit.client_ip("96.63.229.22", "6.6.6.6") == "96.63.229.22"  # a spoofed header changes nothing
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "1")
    # Behind one trusted proxy: its appended entry (rightmost) is the client; a spoofed left entry is ignored.
    assert ratelimit.client_ip("10.0.0.1", "6.6.6.6, 96.63.229.22") == "96.63.229.22"
    assert ratelimit.client_ip("10.0.0.1", None) == "10.0.0.1"


def test_limit_is_per_ip_and_slides(monkeypatch):
    now = [0.0]
    lim = ratelimit.RateLimiter(clock=lambda: now[0])
    monkeypatch.setenv("RATE_LIMIT_PLAN_PER_HOUR", "2")
    lim.check("plan", "1.1.1.1")
    lim.check("plan", "1.1.1.1")
    with pytest.raises(ratelimit.RateLimited) as e:
        lim.check("plan", "1.1.1.1")
    assert e.value.retry_after_s == 3601
    lim.check("plan", "2.2.2.2")  # a different IP has its own limit
    lim.check("run", "1.1.1.1")   # and so does a different endpoint
    now[0] = 3600.0
    lim.check("plan", "1.1.1.1")  # an hour later the first request has left the window


def test_two_ips_do_not_share_a_limit_over_http(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_PLAN_PER_HOUR", "1")
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "1")  # lets the test client present two addresses
    monkeypatch.setenv("APP_MODE", "replay")       # no model call; an unknown question is a 409
    c = TestClient(app)
    ip = lambda a: {"X-Forwarded-For": a}  # noqa: E731
    assert c.post("/plan", json={"question": "q"}, headers=ip("1.1.1.1")).status_code == 409
    r = c.post("/plan", json={"question": "q"}, headers=ip("1.1.1.1"))
    assert r.status_code == 429 and int(r.headers["retry-after"]) > 0
    assert c.post("/plan", json={"question": "q"}, headers=ip("2.2.2.2")).status_code == 409


# ---------- replay mode: recorded runs, no LLM (SPEC §12) ----------

import json as _json  # noqa: E402

import app.agent.orchestrator as orch  # noqa: E402
from app.agent import llm as llm_mod  # noqa: E402
from app.safety import replay  # noqa: E402
from tests.test_orchestrator import GOOD, ScriptedLLM, plan as transition_plan  # noqa: E402


@pytest.fixture
def recorded(tmp_path, monkeypatch):
    """One real (scripted) transition run with a verifier result, curated into a temporary replay set."""
    monkeypatch.setattr(orch, "INVESTIGATIONS_DIR", tmp_path / "inv")
    monkeypatch.setattr(replay, "REPLAY_DIR", tmp_path / "replay")
    monkeypatch.setattr(replay, "PACE", 0.0)
    run = orch.run_investigation(ScriptedLLM(GOOD), orch.new_run("How often do NMP and CORE touch?", transition_plan()))
    run.verification = {"status": "verified", "summary": "Fine.", "flags": [], "wall_s": 1.0}
    run.input_items.append({"type": "reasoning", "encrypted_content": "SECRET"})
    replay.curate(run.as_dict(), label="NMP–CORE contacts")
    return run


def test_curated_run_drops_encrypted_reasoning(recorded):
    text = next(replay.REPLAY_DIR.glob("*.json")).read_text(encoding="utf-8")
    assert "SECRET" not in text and "input_items" not in _json.loads(text)


def test_replay_plays_a_recorded_run_without_any_llm(recorded, monkeypatch):
    monkeypatch.setenv("APP_MODE", "replay")
    monkeypatch.setenv("DEMO_PASSCODE", "s3cret")  # replay needs no passcode: it calls no model

    def no_llm(*a, **k):
        raise AssertionError("an LLM client was created in replay mode")
    monkeypatch.setattr(llm_mod.LLM, "__init__", no_llm)
    c = TestClient(app)
    cat = c.get("/replays").json()
    assert cat["mode"] == "replay" and cat["recorded"][0]["label"] == "NMP–CORE contacts"
    p = c.post("/plan", json={"question": "how often do NMP and CORE touch"}).json()  # case/punctuation ignored
    assert p["plan_id"] == f"replay:{recorded.run_id}" and p["recorded"] is True
    rid = c.post("/runs", json={"plan_id": p["plan_id"]}).json()["run_id"]
    with c.stream("GET", f"/runs/{rid}/events") as s:
        events = [_json.loads(line[6:]) for line in s.iter_lines() if line.startswith("data: ")]
    assert [e["type"] for e in events][-1] == "verification_finished"
    assert sum(e["type"] == "turn" for e in events) == len(recorded.turns)
    v = c.get(f"/runs/{rid}").json()
    assert v["recorded_from"] == recorded.run_id and v["status"] == "verified"
    assert v["report"]["findings"][0]["rendered"] == recorded.state.report["findings"][0]["rendered"]
    miss = c.post("/plan", json={"question": "Something never recorded?"})
    assert miss.status_code == 409 and miss.json()["detail"]["recorded"][0]["run_id"] == recorded.run_id
    assert c.get("/health").json()["mode"] == "replay"
