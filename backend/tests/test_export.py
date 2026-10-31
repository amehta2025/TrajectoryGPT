"""Reproducible export: the generated script replays a run in a fresh interpreter and matches every value."""
import ast
import json
import subprocess
import sys

import pytest

import app.agent.orchestrator as orch
import app.agent.report as report
import app.agent.tools as tools
import app.science.contact_stats as cs
from app.agent.tools import ApprovedPlan
from app.config import dataset_config, load_config
from app.provenance import export
from tests.test_orchestrator import ScriptedLLM

DS = "adk_transition"
PAIR = "ASP54-LYS157"


@pytest.fixture(autouse=True)
def tmp_investigations(tmp_path, monkeypatch):
    monkeypatch.setattr(orch, "INVESTIGATIONS_DIR", tmp_path)


def stats_config() -> dict:
    """The transition set refuses statistics; the export test pretends it doesn't, to cover the stats tools."""
    cfg = json.loads(json.dumps(load_config(), default=str))
    cfg["datasets"][DS]["stats_valid"] = True
    return cfg


def make_run(monkeypatch) -> dict:
    fake = stats_config()["datasets"]
    for mod in (tools, cs, report):
        monkeypatch.setattr(mod, "dataset_config", lambda ds: fake[ds])
    steps = [{"tool": t, "rationale": ""} for t in ("calculate_contacts", "compare_intervals", "cutoff_sensitivity",
                                                     "native_contacts", "submit_report")]
    plan = ApprovedPlan(DS, "NMP", "LID", 4.5, [0, 49], [49, 98], steps=steps)
    script = [("calculate_contacts", {"window": "all"}),
              ("compare_intervals", {}),
              ("cutoff_sensitivity", {}),
              ("native_contacts", {"selection": "name CA", "radius": 8.0}),
              ("distance_over_time", {"pair": PAIR, "window": "all"}),
              ("record_finding", {"text": "They touch in {{r1.any_contact_freq}} of frames; {{r2.n_tested}} pairs tested.",
                                  "plain": "They touch in {{r1.any_contact_freq|pct}} of frames.",
                                  "evidence_ids": ["r1"], "claim": "descriptive"}),
              ("submit_report", {"finding_ids": ["f1"], "headline": "They touch in {{r1.any_contact_freq|pct}} of frames."})]
    run = orch.run_investigation(ScriptedLLM(script), orch.new_run("How often do NMP and LID touch?", plan))
    assert run.status == "awaiting_verification", run.problems
    return run.as_dict()


def test_script_is_generated_from_the_template(monkeypatch):
    run = make_run(monkeypatch)
    src = export.build_script(run, stats_config())
    ast.parse(src)  # valid Python
    assert "@@" not in src and export.script_name(run["run_id"]) == f"reproduce_{run['run_id']}.py"
    payload = export.run_payload(run)
    assert [c["tool"] for c in payload["calls"]] == ["calculate_contacts", "compare_intervals", "cutoff_sensitivity",
                                                     "native_contacts", "distance_over_time"]
    assert payload["cited"] == ["r1.any_contact_freq", "r2.n_tested"]
    assert payload["dataset_hashes"] and payload["seed"] == 0


def test_script_reproduces_every_value_in_a_fresh_interpreter(monkeypatch, tmp_path):
    run = make_run(monkeypatch)
    path = tmp_path / export.script_name(run["run_id"])
    path.write_text(export.build_script(run, stats_config()), encoding="utf-8")
    p = subprocess.run([sys.executable, "-I", str(path), "--out", str(tmp_path / "out"), "--data-dir", str(tmp_path / "data")],
                       capture_output=True, text=True, encoding="utf-8", cwd=tmp_path, timeout=600)
    assert p.returncode == 0, p.stdout[-3000:] + p.stderr[-3000:]
    assert "REPRODUCED" in p.stdout and "MISMATCH" not in p.stdout
    assert (tmp_path / "out" / "r2_compare_intervals.png").exists()


def test_a_changed_value_is_reported_as_a_mismatch(monkeypatch, tmp_path):
    run = make_run(monkeypatch)
    run["results"]["r1"]["values"]["any_contact_freq"]["v"] += 0.01
    path = tmp_path / "reproduce.py"
    path.write_text(export.build_script(run, stats_config()), encoding="utf-8")
    p = subprocess.run([sys.executable, "-I", str(path), "--out", str(tmp_path / "out")], capture_output=True, text=True,
                       encoding="utf-8", cwd=tmp_path, timeout=600)
    assert p.returncode == 1 and "r1.any_contact_freq" in p.stdout and "MISMATCH" in p.stdout


def test_run_without_results_cannot_be_exported():
    with pytest.raises(ValueError):
        export.build_script({"run_id": "x", "question": "q", "plan": {"dataset": DS}, "turns": [], "results": {}})
    assert dataset_config(DS)["stats_valid"] is False  # the real config is untouched


def test_export_and_sandbox_endpoints(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    run = make_run(monkeypatch)
    c = TestClient(app)
    r = c.get(f"/runs/{run['run_id']}/export")
    assert r.status_code == 200 and r.headers["content-disposition"].endswith(f'reproduce_{run["run_id"]}.py"')
    ast.parse(r.text)
    monkeypatch.delenv("SANDBOX_ENABLED", raising=False)
    monkeypatch.delenv("DEMO_PASSCODE", raising=False)
    assert c.post(f"/runs/{run['run_id']}/sandbox").status_code == 503  # deployed app only
    assert c.get(f"/runs/{run['run_id']}/sandbox").json() == {"status": "none"}
    assert c.get("/runs/nope/export").status_code == 404
