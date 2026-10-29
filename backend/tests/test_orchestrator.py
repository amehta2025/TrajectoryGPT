"""Turn loop and planner validation with a scripted fake LLM (no network), on adk_transition."""
import json

import pytest

import app.agent.orchestrator as orch
from app.agent.llm import CostTracker, ToolCall, TurnResult, Usage
from app.agent.planner import PlanInvalid, to_approved, validate_plan
from app.agent.tools import ApprovedPlan

DS = "adk_transition"


class ScriptedLLM:
    """Returns the next scripted (tool, args) per turn; None means a text reply with no tool call."""

    def __init__(self, script, cost=0.001):
        self.script, self.cost, self.seen = list(script), cost, []
        self.tracker = CostTracker(limit_usd=0.01)

    def run_turn(self, input_items, tools, *, schema=None, effort):
        self.seen.append(len(input_items))
        step = self.script.pop(0)
        u = Usage("meta", "fake", effort, latency_s=0.01, cost_usd=self.cost)
        self.tracker.add(u)
        if step is None:
            return TurnResult([], "thinking", None, [{"type": "message", "role": "assistant",
                                                      "content": [{"type": "output_text", "text": "thinking"}]}], u)
        name, args = step
        cid = f"c{len(self.seen)}"
        item = {"type": "function_call", "call_id": cid, "name": name, "arguments": json.dumps(args)}
        return TurnResult([ToolCall(cid, name, json.dumps(args))], "", None, [item], u)


@pytest.fixture(autouse=True)
def tmp_investigations(tmp_path, monkeypatch):
    monkeypatch.setattr(orch, "INVESTIGATIONS_DIR", tmp_path)
    return tmp_path


def plan():
    return ApprovedPlan(DS, "NMP", "CORE", 4.5, [0, 98], None,
                        steps=[{"tool": "calculate_contacts", "rationale": "map"}, {"tool": "submit_report", "rationale": ""}])


GOOD = [("calculate_contacts", {}),
        ("record_finding", {"text": "{{r1.n_pairs_in_contact}} NMP-CORE pairs touch", "evidence_ids": ["r1"], "claim": "descriptive"}),
        ("submit_report", {"finding_ids": ["f1"], "gaps": ["statistics are not available on this dataset"]})]


def test_happy_path(tmp_investigations):
    events = []
    run = orch.run_investigation(ScriptedLLM(GOOD), orch.new_run("q", plan()), on_event=events.append)
    assert run.status == "awaiting_verification"
    assert [t["tool"] for t in run.turns] == ["calculate_contacts", "record_finding", "submit_report"]
    assert all(c["status"] == "done" for c in run.state.checklist)
    assert "{{" not in run.state.report["findings"][0]["rendered"]
    assert events[-1]["type"] == "run_finished" and events[-2]["progress"] == "2/2 done"
    saved = json.loads((tmp_investigations / f"{run.run_id}.json").read_text(encoding="utf-8"))
    assert saved["turns"][0]["saw"][0]["role"] == "system"  # the timeline keeps what the model saw
    assert saved["turns"][1]["saw"][0]["type"] == "function_call_output"
    assert run.turns[0]["model_s"] >= 0 and "tool_s" in run.turns[0]


def test_nudge_when_no_tool_call():
    run = orch.run_investigation(ScriptedLLM([None] + GOOD), orch.new_run("q", plan()))
    assert run.status == "awaiting_verification"
    assert run.turns[0]["status"] == "no_tool_call"
    assert run.input_items[3]["content"] == "Call exactly one tool."


def test_two_refused_reports_need_review():
    bad = ("submit_report", {"finding_ids": ["f9"]})
    run = orch.run_investigation(ScriptedLLM([GOOD[0], bad, bad, GOOD[1]]), orch.new_run("q", plan()))
    assert run.status == "needs_review"
    assert len(run.turns) == 3 and any("f9" in p for p in run.problems)


def test_turn_limit_needs_review():
    llm = ScriptedLLM([("get_metadata", {})] * 3)
    run = orch.run_investigation(llm, orch.new_run("q", plan()), max_turns=3)
    assert run.status == "needs_review" and "no accepted report" in run.problems[0]
    assert run.input_items[-3]["content"].startswith("Last turn")


def test_budget_aborts_run():
    run = orch.run_investigation(ScriptedLLM(GOOD, cost=0.006), orch.new_run("q", plan()))
    assert run.status == "aborted_budget" and "exceeds" in run.error


def test_resume_continues_saved_run():
    run = orch.run_investigation(ScriptedLLM(GOOD[:1]), orch.new_run("q", plan()), max_turns=1)
    assert run.status == "needs_review"
    resumed = orch.resume_run(ScriptedLLM(GOOD[1:]), run.run_id, max_turns=3)
    assert resumed.status == "awaiting_verification" and len(resumed.turns) == 3


# ---------- planner validation (code side, no LLM) ----------

def plan_dict(**kw):
    p = {"summary": "", "dataset": "adk_equilibrium", "group_a": "LID", "group_b": "CORE", "cutoff": 4.5,
         "window_a": [0, 2093], "window_b": [2093, 4187], "stride": 1, "sequence_exclusion": 2,
         "robustness_cutoffs": [4.0, 4.5, 5.0],
         "steps": [{"tool": t, "rationale": ""} for t in ("compare_intervals", "cutoff_sensitivity", "submit_report")]}
    return {**p, **kw}


def test_valid_plan_converts(monkeypatch):
    import app.agent.planner as planner
    monkeypatch.setattr(planner, "load_universe", lambda ds: type("U", (), {"trajectory": type("T", (), {"n_frames": 4187})})())
    ap = to_approved(plan_dict())
    assert ap.robustness_cutoffs == [4.0, 4.5, 5.0] and ap.window_b == [2093, 4187]


@pytest.mark.parametrize("kw,msg", [
    ({"dataset": DS, "window_a": [0, 49], "window_b": [49, 98]}, "stats_valid: false"),
    ({"window_b": [2000, 4187]}, "overlap"),
    ({"cutoff": 12.0, "robustness_cutoffs": [4.0, 5.0]}, "between"),
    ({"robustness_cutoffs": [4.0, 5.0]}, "must be one of"),
    ({"window_b": None}, "need two windows"),
    ({"steps": [{"tool": "compare_intervals", "rationale": ""}]}, "submit_report"),
    ({"group_b": "LID"}, "must differ"),
])
def test_invalid_plans(kw, msg, monkeypatch):
    import app.agent.planner as planner
    n = 98 if kw.get("dataset") == DS else 4187
    monkeypatch.setattr(planner, "load_universe", lambda ds: type("U", (), {"trajectory": type("T", (), {"n_frames": n})})())
    errs = validate_plan(plan_dict(**kw))
    assert any(msg in e for e in errs), errs
    with pytest.raises(PlanInvalid):
        to_approved(plan_dict(**kw))
