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


def test_two_refused_reports_fall_back_to_the_computed_report():
    """A judge never sees "needs review" over wording: the report is built by code from the computed results,
    with a plain note; the technical reasons are kept for Show the science."""
    bad = ("submit_report", {"finding_ids": ["f9"]})
    run = orch.run_investigation(ScriptedLLM([GOOD[0], bad, bad, GOOD[1]]), orch.new_run("q", plan()))
    assert run.status == "awaiting_verification" and len(run.turns) == 3
    rep = run.state.report
    assert rep["fallback"]["note"].startswith("Some of the AI's wording couldn't be verified")
    assert any("f9" in p for p in rep["fallback"]["removed"])
    assert rep["findings"] and all(f.get("code_built") for f in rep["findings"]) and "{{" not in rep["findings"][0]["rendered"]


def test_nothing_computed_still_needs_review():
    bad = ("submit_report", {"finding_ids": ["f9"]})
    run = orch.run_investigation(ScriptedLLM([("get_metadata", {}), bad, bad]), orch.new_run("q", plan()))
    assert run.status == "needs_review"


def test_turn_limit_needs_review():
    llm = ScriptedLLM([("get_metadata", {})] * 3)
    run = orch.run_investigation(llm, orch.new_run("q", plan()), max_turns=3)
    assert run.status == "needs_review" and "no accepted report" in run.problems[0]
    assert run.input_items[-3]["content"].startswith("Last turn")


def test_budget_aborts_run():
    run = orch.run_investigation(ScriptedLLM(GOOD, cost=0.006), orch.new_run("q", plan()))
    assert run.status == "aborted_budget" and "exceeds" in run.error


def test_resume_continues_saved_run():
    run = orch.run_investigation(ScriptedLLM([("get_metadata", {})]), orch.new_run("q", plan()), max_turns=1)
    assert run.status == "needs_review"  # nothing computed yet, so no fallback report
    resumed = orch.resume_run(ScriptedLLM(GOOD), run.run_id, max_turns=4)
    assert resumed.status == "awaiting_verification" and len(resumed.turns) == 4


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


# ---------- regression: "Explain this simulation to me" on adk_transition (stats refused) ----------

EXPLAIN_PLAN = [{"tool": t, "rationale": ""} for t in ("get_metadata", "native_contacts", "submit_report")]


def explain_plan():
    return ApprovedPlan(DS, "LID", "NMP", 4.5, [0, 98], None, steps=EXPLAIN_PLAN)


def test_explain_transition_without_a_finding_needs_review():
    """The original failure: no statistical result, so the agent submitted with no finding at all."""
    script = [("get_metadata", {}), ("compare_intervals", {}), ("submit_report", {"finding_ids": []}),
              ("submit_report", {"finding_ids": []})]
    run = orch.run_investigation(ScriptedLLM(script), orch.new_run("Explain this simulation to me", explain_plan()))
    assert run.turns[1]["status"] == "refused"  # stats refused on the transition path
    assert run.status == "needs_review" and "cite at least one finding id" in run.problems


def test_explain_transition_with_descriptive_finding_and_background():
    script = [
        ("get_metadata", {}),
        ("native_contacts", {}),
        ("record_finding", {"text": "Along the path, the fraction of native contacts of the first frame falls from "
                                    "{{r2.ref0.q_first}} to {{r2.ref0.q_last}} over {{r1.n_frames}} path steps.",
                            "evidence_ids": ["r2", "r1"], "claim": "descriptive"}),
        ("submit_report", {"finding_ids": ["f1"],
                           "background": ["Adenylate kinase closes its LID and NMP domains over ATP and AMP and reopens."],
                           "gaps": ["Statistics are not meaningful on this biased path."]}),
    ]
    run = orch.run_investigation(ScriptedLLM(script), orch.new_run("Explain this simulation to me", explain_plan()))
    assert run.status == "awaiting_verification", run.problems
    rep = run.state.report
    f = rep["findings"][0]
    assert f["badge"] == "descriptive" and f["badge_label"] == "descriptive — not statistically tested"
    assert "{{" not in f["rendered"] and not f["robustness"]
    assert rep["background"][0]["text"].startswith("Adenylate kinase") and rep["background_label"] == "background — not computed"
    assert any("no statistics were computed" in c["text"] for c in rep["caveats"])
    assert not any(c["text"].startswith("Limited sampling") for c in rep["caveats"])


@pytest.mark.parametrize("text", ["The path shows a significant loss of native contacts {{r1.ref0.q_last}}",
                                  "A robust drop to {{r1.ref0.q_last}}", "The drop to {{r1.ref0.q_last}} is supported"])
def test_descriptive_findings_cannot_claim_support(text):
    script = [("native_contacts", {}), ("record_finding", {"text": text, "evidence_ids": ["r1"], "claim": "descriptive"}),
              ("submit_report", {"finding_ids": ["f1"]}), ("submit_report", {"finding_ids": ["f1"]})]
    run = orch.run_investigation(ScriptedLLM(script), orch.new_run("q", explain_plan()))
    rep = run.state.report  # the overclaiming sentence is removed; the report is built from the computed result
    assert run.status == "awaiting_verification" and rep["fallback"]
    assert any("descriptive finding is not statistically tested" in p for p in rep["fallback"]["removed"])
    assert not any(w in f["rendered"].lower() for f in rep["findings"] for w in ("significant", "robust", "supported"))


# ---------- persistence: runs are saved as they progress and read back from disk ----------

def test_run_saved_from_the_start_and_listed(tmp_investigations):
    run = orch.new_run("q", plan())
    orch.run_investigation(ScriptedLLM(GOOD[:1]), run, max_turns=1)
    listed = orch.list_runs()
    assert listed[0]["run_id"] == run.run_id and listed[0]["dataset"] == DS and listed[0]["question"] == "q"
    assert not list(tmp_investigations.glob("*.tmp"))


def test_provenance_records_the_model_call():
    run = orch.run_investigation(ScriptedLLM(GOOD), orch.new_run("q", plan()))
    p = run.state.results["r1"]["provenance"]
    assert p["model"] == "fake" and p["turn"] == 1 and p["usage"]["effort"] == "low"


def test_turn_budget_leaves_room_for_findings_and_the_report():
    steps = lambda n: [{"tool": "calculate_contacts", "rationale": ""}] * n + [{"tool": "submit_report", "rationale": ""}]  # noqa: E731
    mk = lambda n: ApprovedPlan(DS, "NMP", "CORE", 4.5, [0, 98], None, steps=steps(n))  # noqa: E731
    assert orch.turn_budget(mk(1)) == 8 and orch.turn_budget(mk(3)) == 8
    assert orch.turn_budget(mk(4)) == 9  # 4 analyses + 3 findings + report + 1 spare
    assert orch.turn_budget(mk(6)) == 10


# ---------- plan settings are not bare numbers; numbers code can stand behind are repaired ----------

def paper01_plan():
    return ApprovedPlan("adk_equilibrium", "LID", "CORE", 4.0, [2104, 4187], None, sequence_exclusion=3,
                        residue_ranges={"LID": [122, 159], "CORE": [100, 121]},
                        steps=[{"tool": "calculate_contacts", "rationale": ""}, {"tool": "submit_report", "rationale": ""}])


def test_plan_settings_are_placeholders_and_not_bare_numbers():
    """The live refusal: a follow-up on paper 01 wrote "last 500 ns" and "3 residues" (approved settings)."""
    from app.agent.report import bare_numbers, render
    from app.agent.tools import RunState
    s = RunState(paper01_plan())
    assert bare_numbers(s, "over the last 500 ns, skipping pairs within 3 residues, CORE 100-121 at 4.0 Å") == []
    assert bare_numbers(s, "0.527 of frames") == ["0.527"]  # a computed result still needs its placeholder
    text, _ = render(s, "the last {{plan.window_a_length_ns}} at {{plan.cutoff}}; CORE {{plan.range_CORE_first}}")
    assert text == "the last 500 ns at 4 Å; CORE 100"


def test_numbers_equal_to_a_cited_value_are_repaired_into_placeholders():
    from app.agent.report import autorepair
    from app.agent.tools import RunState, run_tool
    s = RunState(plan())
    run_tool(s, "calculate_contacts", "{}")
    n = s.results["r1"]["values"]["n_pairs_in_contact"]["v"]
    pct = s.results["r1"]["values"]["any_contact_freq"]["v"]
    from app.agent.report import format_pct
    fixed = autorepair(s, f"{n} pairs touch, in {format_pct(pct, 'freq')} of frames; 0.5 is unexplained", ["r1"])
    assert "{{r1.n_pairs_in_contact}}" in fixed and "{{r1.any_contact_freq|pct}}" in fixed and "0.5" in fixed


def test_a_quiet_turn_after_a_refused_report_gets_one_more_nudge():
    bad = ("submit_report", {"finding_ids": ["f9"]})
    script = [GOOD[0], GOOD[1], bad, None, GOOD[2]]
    run = orch.run_investigation(ScriptedLLM(script), orch.new_run("q", plan()), max_turns=4)
    assert run.status == "awaiting_verification" and not run.state.report.get("fallback")
    nudge = next(i["content"] for i in run.input_items if isinstance(i.get("content"), str) and i["content"].startswith("Your submit_report was refused"))
    assert "f9" in nudge
