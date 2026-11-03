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
    assert view["report"]["findings"][0]["badge"] == "descriptive" and "input_items" not in view
    assert view["turns"][0]["saw"][0]["role"] == "system"


def test_edits_are_validated():
    c = TestClient(app)
    r = c.post("/runs", json={"plan_id": store_plan(GOOD), "edits": {"cutoff": 12}})
    assert r.status_code == 422 and any("cutoff" in e for e in r.json()["detail"]["errors"])
    assert c.post("/runs", json={"plan_id": "nope"}).status_code == 404


def test_one_plan_starts_one_run():
    """A retry, double click or script posting the same plan gets the first run back, not a second paid run."""
    import threading
    c = TestClient(app)
    pid = store_plan(GOOD)
    before = len(session.RUNS)
    ids = []
    workers = [threading.Thread(target=lambda: ids.append(c.post("/runs", json={"plan_id": pid}).json()["run_id"]))
               for _ in range(4)]
    for w in workers:
        w.start()
    for w in workers:
        w.join()
    assert len(set(ids)) == 1 and len(session.RUNS) == before + 1
    list(events(c, ids[0]))
    assert c.post("/runs", json={"plan_id": pid, "edits": {"cutoff": 5.0}}).json()["run_id"] == ids[0]


def test_a_refused_start_leaves_the_plan_unclaimed():
    c = TestClient(app)
    pid = store_plan(GOOD)
    assert c.post("/runs", json={"plan_id": pid, "edits": {"cutoff": 12}}).status_code == 422
    rid = c.post("/runs", json={"plan_id": pid, "edits": {}}).json()["run_id"]
    assert list(events(c, rid))[-1]["type"] == "run_finished"


def test_step_through_pauses_until_next():
    c = TestClient(app)
    rid = c.post("/runs", json={"plan_id": store_plan(GOOD), "step_through": True}).json()["run_id"]
    seen = []
    for e in events(c, rid):
        seen.append(e["type"])
        if e["type"] == "paused":
            assert c.post(f"/runs/{rid}/next", json={}).json() == {"ok": True}
    assert seen.count("paused") == 2 and seen[-1] == "run_finished"


def test_finished_run_is_read_back_from_disk_after_restart():
    c = TestClient(app)
    rid = c.post("/runs", json={"plan_id": store_plan(GOOD), "edits": {}}).json()["run_id"]
    list(events(c, rid))
    session.RUNS.clear()  # as after a container restart
    view = c.get(f"/runs/{rid}").json()
    assert view["status"] == "awaiting_verification" and view["report"]["findings"] and view["wall_s"] > 0
    evs = list(events(c, rid))
    assert evs == [{**evs[0], "type": "run_finished", "status": "awaiting_verification"}]
    assert c.get("/runs/nope").status_code == 404
    assert c.get("/runs").json()[0]["run_id"] == rid


def propose(monkeypatch) -> str:
    """A plan made the way /plan makes it (planner stubbed), so it is saved to the plan store."""
    llm = ScriptedLLM([])
    llm.provider, llm.model = "meta", "fake"
    monkeypatch.setattr(session, "make_plan", lambda *a, **k: (PLAN, []))
    return session.propose_plan("q", "adk_transition", llm=llm, skip_scope=True)["plan_id"]


def restart_container(monkeypatch, script=GOOD):
    """What a scale-down, redeploy or second container sees: nothing in memory, only the store on disk."""
    session.PLANS.clear()
    session._PLAN_LOCKS.clear()
    monkeypatch.setattr(session, "new_llm", lambda provider=None: ScriptedLLM(script))


def test_a_plan_runs_after_the_container_restarts(monkeypatch):
    """The live bug: Run after the container that made the plan had gone gave 404 'unknown plan'."""
    c = TestClient(app)
    pid = propose(monkeypatch)
    saved = json.loads((session.PLANS_DIR / f"{pid}.json").read_text(encoding="utf-8"))
    assert saved["plan"] == PLAN and saved["provider"] == "meta" and "llm" not in saved
    restart_container(monkeypatch)
    r = c.post("/runs", json={"plan_id": pid, "edits": {}})
    assert r.status_code == 200
    evs = list(events(c, r.json()["run_id"]))
    assert evs[-1]["type"] == "run_finished" and evs[-1]["status"] == "awaiting_verification"


def test_one_plan_one_run_across_containers(monkeypatch):
    """A second container (or the same one after a restart) gets the first run back, never a second paid run."""
    c = TestClient(app)
    pid = propose(monkeypatch)
    restart_container(monkeypatch)
    rid = c.post("/runs", json={"plan_id": pid}).json()["run_id"]
    list(events(c, rid))
    restart_container(monkeypatch, script=[])  # an empty script fails loudly if a second run were started
    before = len(session.RUNS)
    assert c.post("/runs", json={"plan_id": pid, "edits": {"cutoff": 5.0}}).json()["run_id"] == rid
    assert len(session.RUNS) == before


def test_a_lost_plan_says_expired_so_the_ui_replans():
    c = TestClient(app)
    r = c.post("/runs", json={"plan_id": "07a5eac55b8d"})
    assert r.status_code == 404 and r.json()["detail"] == {"plan_expired": True, "message": "This plan expired."}
    assert c.post("/runs", json={"plan_id": "../../etc/passwd"}).json()["detail"]["plan_expired"]  # never read as a path
