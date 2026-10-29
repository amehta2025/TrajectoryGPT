"""Agent HTTP endpoints with a scripted LLM: approve a stored plan, stream events, step through, read the run."""
import json

import pytest
from fastapi.testclient import TestClient

import app.agent.orchestrator as orch
from app.agent import session
from app.main import app
from tests.test_orchestrator import GOOD, ScriptedLLM

PLAN = {"summary": "", "dataset": "adk_transition", "group_a": "NMP", "group_b": "CORE", "cutoff": 4.5,
        "window_a": [0, 98], "window_b": None, "stride": 1, "sequence_exclusion": 2,
        "robustness_cutoffs": [4.0, 4.5, 5.0],
        "steps": [{"tool": "calculate_contacts", "rationale": ""}, {"tool": "submit_report", "rationale": ""}]}


@pytest.fixture(autouse=True)
def tmp_investigations(tmp_path, monkeypatch):
    monkeypatch.setattr(orch, "INVESTIGATIONS_DIR", tmp_path)


def store_plan(script) -> str:
    session.PLANS["p1"] = {"question": "q", "dataset": "adk_transition", "plan": PLAN, "usage": [], "llm": ScriptedLLM(script)}
    return "p1"


def events(client, run_id):
    with client.stream("GET", f"/runs/{run_id}/events") as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        for line in r.iter_lines():
            if line.startswith("data: "):
                e = json.loads(line[6:])
                yield e
                if e["type"] == "run_finished":
                    return


def test_run_streams_and_reports():
    c = TestClient(app)
    rid = c.post("/runs", json={"plan_id": store_plan(GOOD), "edits": {}}).json()["run_id"]
    evs = list(events(c, rid))
    assert [e["tool"] for e in evs if e["type"] == "turn"] == ["calculate_contacts", "record_finding", "submit_report"]
    assert evs[-1]["status"] == "awaiting_verification"
    view = c.get(f"/runs/{rid}").json()
    assert view["report"]["findings"][0]["badge"] == "observed" and "input_items" not in view
    assert view["turns"][0]["saw"][0]["role"] == "system"


def test_edits_are_validated():
    c = TestClient(app)
    r = c.post("/runs", json={"plan_id": store_plan(GOOD), "edits": {"cutoff": 12}})
    assert r.status_code == 422 and any("cutoff" in e for e in r.json()["detail"]["errors"])
    assert c.post("/runs", json={"plan_id": "nope"}).status_code == 404


def test_step_through_pauses_until_next():
    c = TestClient(app)
    rid = c.post("/runs", json={"plan_id": store_plan(GOOD), "step_through": True}).json()["run_id"]
    seen = []
    for e in events(c, rid):
        seen.append(e["type"])
        if e["type"] == "paused":
            assert c.post(f"/runs/{rid}/next", json={}).json() == {"ok": True}
    assert seen.count("paused") == 2 and seen[-1] == "run_finished"
