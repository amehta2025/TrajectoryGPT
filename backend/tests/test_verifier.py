"""Muse Wonder verifier: input, plots, planted overclaim and statuses, with a fake LLM (no network)."""
import pytest

from app.agent import report, verifier
from app.agent.llm import CostTracker, TurnResult, Usage
from app.agent.orchestrator import new_run
from app.agent.session import RunHandle
from app.agent.tools import ApprovedPlan

DS = "adk_transition"
KA, KB, KC = f"{DS}:4AKE:30:SER", f"{DS}:4AKE:84:ASP", f"{DS}:4AKE:118:ASP"


def _row(ka, kb, delta, supported):
    return {"key_a": ka, "key_b": kb, "freq_a": 0.46, "freq_b": 0.46 + delta, "delta": delta, "ci_low": delta - 0.25,
            "ci_high": delta + 0.25, "q": 0.01 if supported else 0.31, "supported": supported, "provisional": not supported,
            "n_blocks_a": 25.0, "n_blocks_b": 18.0}


@pytest.fixture
def run():
    """A run with an accepted report and one compare_intervals result: SER30-ASP84 large but unsupported."""
    plan = ApprovedPlan(DS, "NMP", "CORE", 4.5, [0, 49], [49, 98],
                        steps=[{"tool": "compare_intervals", "rationale": ""}, {"tool": "submit_report", "rationale": ""}])
    r = new_run("Which contacts change?", plan, run_id="test-run")
    rows = [_row(KA, KB, -0.31, False), _row(KA, KC, 0.05, False)]
    r.state.results["r1"] = {
        "status": "ok", "tool": "compare_intervals", "provenance_id": "r1", "data_for_ui": {"rows": rows},
        "values": {"n_tested": {"v": 2, "kind": "count"}, "n_supported": {"v": 0, "kind": "count"},
                   "SER30-ASP84.delta": {"v": -0.31, "kind": "delta"}},
        "pairs": {"SER30-ASP84": {"key_a": KA, "key_b": KB, "supported": False, "provisional": True, "delta": -0.31},
                  "SER30-ASP118": {"key_a": KA, "key_b": KC, "supported": False, "provisional": True, "delta": 0.05}}}
    r.state.findings["f1"] = {"id": "f1", "text": "None of the {{r1.n_tested}} tested pairs is supported.",
                              "evidence_ids": ["r1"], "claim": "null"}
    r.state.report = report.build_report(r.state, {"finding_ids": ["f1"]})
    r.status = "awaiting_verification"
    return r


class FakeWonder:
    def __init__(self, parsed=None, fail=False):
        self.parsed, self.fail, self.inputs = parsed, fail, None
        self.tracker = CostTracker()

    def run_turn(self, input_items, tools=None, *, schema=None, effort):
        self.inputs = input_items
        if self.fail:
            raise RuntimeError("boom")
        assert schema is verifier.SCHEMA and effort == "high"
        return TurnResult([], "", self.parsed, [], Usage("meta", "muse-wonder-1.0", effort, 1000, 0, 200, 150, 3.0, 0.008))


def test_plant_overclaim_misdescribes_the_largest_unsupported_pair(run):
    fid = verifier.plant_overclaim(run)
    assert fid == "f2" and run.planted == "f2"
    planted = run.state.report["findings"][-1]
    assert planted["claim"] == "supported" and planted["evidence_ids"] == ["r1.SER30-ASP84"]
    assert "drops significantly" in planted["rendered"] and "{{" not in planted["rendered"]
    # Planted after acceptance: the deterministic check would have refused it.
    assert any("not supported" in p for p in report.finding_problems(run.state, fid, run.state.findings[fid]))


def test_verifier_sees_stored_values_and_one_plot_per_cited_result(run):
    verifier.plant_overclaim(run)
    doc = verifier.verifier_input(run)
    ev = doc["findings"][-1]["evidence"][0]
    assert ev["values"]["supported"] is False and ev["values"]["q"] == 0.31
    assert doc["dataset"]["stats_valid"] is False
    figs = verifier.figures(run)
    assert [pid for pid, _ in figs] == ["r1"] and len(figs[0][1]) > 1000  # base64 PNG


def test_major_flag_means_flagged(run):
    llm = FakeWonder({"summary": "f2 overclaims.", "flags": [
        {"finding_id": "f2", "quote": "drops significantly", "problem": "q = 0.31: not supported.", "plain": "It calls this change real, but chance can explain it.", "severity": "major"}]})
    verifier.plant_overclaim(run)
    v = verifier.verify_run(run, llm)
    assert v["status"] == "flagged" and run.status == "flagged"
    assert v["usage"]["cost_usd"] == 0.008 and run.verifier_cost_usd() == 0.008
    user = llm.inputs[1]["content"]
    assert any(p["type"] == "input_image" and p["image_url"].startswith("data:image/png;base64,") for p in user)


def test_minor_flags_only_means_verified(run):
    llm = FakeWonder({"summary": "Fine.", "flags": [
        {"finding_id": "f1", "quote": "x", "problem": "wording", "plain": "Wording.", "severity": "minor"}]})
    assert verifier.verify_run(run, llm)["status"] == "verified" and run.status == "verified"


def test_failed_check_is_an_error_not_verified(run):
    v = verifier.verify_run(run, FakeWonder(fail=True))
    assert v["status"] == "error" and "boom" in v["error"]
    assert run.status == "awaiting_verification"


def test_stream_stays_open_until_verification_finishes():
    h = RunHandle(run=None)
    h.emit({"type": "run_finished", "status": "awaiting_verification"})
    assert not h.done
    h.emit({"type": "verification_finished", "status": "verified"})
    assert h.done
    h2 = RunHandle(run=None)
    h2.emit({"type": "run_finished", "status": "needs_review"})
    assert h2.done


def test_planting_needs_the_dev_flag(monkeypatch):
    monkeypatch.delenv("ALLOW_PLANTED_OVERCLAIM", raising=False)
    assert not verifier.planting_allowed()
    monkeypatch.setenv("ALLOW_PLANTED_OVERCLAIM", "1")
    assert verifier.planting_allowed()


def test_result_level_evidence_includes_the_values_the_text_uses(run):
    run.state.results["r1"]["values"]["SER30-ASP84.freq_a"] = {"v": 0.46, "kind": "freq"}
    run.state.findings["f1"]["text"] = "SER30-ASP84 is in contact in {{r1.SER30-ASP84.freq_a}} of frames."
    run.state.report = report.build_report(run.state, {"finding_ids": ["f1"]})
    vals = verifier.verifier_input(run)["findings"][0]["evidence"][0]["values"]
    assert vals["SER30-ASP84.freq_a"] == 0.46 and vals["n_tested"] == 2


class SlowWonder(FakeWonder):
    def run_turn(self, *a, **k):
        import time
        time.sleep(0.6)
        return super().run_turn(*a, **k)


def test_check_that_runs_past_the_limit_is_shown_as_not_finished(run, monkeypatch):
    from app.agent import session
    monkeypatch.setattr(verifier, "TIMEOUT_S", 0.2)
    late = SlowWonder({"summary": "Fine.", "flags": []})
    monkeypatch.setattr(verifier, "verifier_llm", lambda: late)
    monkeypatch.setattr(type(run), "save", lambda self: None)
    events = []
    session.verify_in_background(run, events.append)
    assert events[-1]["type"] == "verification_finished" and events[-1]["verification"]["status"] == "timeout"
    import time
    time.sleep(0.8)  # the slow check finishes afterwards...
    assert run.verification["status"] == "timeout" and run.status == "awaiting_verification"  # ...and changes nothing


def test_saved_pending_check_without_a_live_thread_is_not_endless(monkeypatch, tmp_path, run):
    import app.agent.orchestrator as orch
    from app.agent import session
    monkeypatch.setattr(orch, "INVESTIGATIONS_DIR", tmp_path)
    run.verification = verifier.pending()
    run.save()
    assert session.run_view(run.run_id)["verification"]["status"] == "timeout"  # e.g. after a server restart
    assert verifier.resolve_stale(verifier.pending(), live=True)["status"] == "pending"  # a live check keeps spinning


# ---------- sweep failure classes: what the verifier is shown ----------

def test_cited_contact_map_pairs_carry_their_values_and_plan_settings_are_listed():
    """Class: paper 01 was flagged for "empty stored values" (pairs cited from calculate_contacts) and a vague
    question for a run length that came from an approved plan setting."""
    from app.agent.tools import ApprovedPlan, RunState, run_tool
    from app.agent.verifier import _pair_facts
    s = RunState(ApprovedPlan("adk_transition", "LID", "CORE", 4.5, [0, 98], steps=[{"tool": "calculate_contacts"}, {"tool": "submit_report"}]))
    r = run_tool(s, "calculate_contacts", "{}")
    pair = next(iter(s.results["r1"]["pairs"]))
    assert "freq" in _pair_facts(s.results["r1"], pair)


def test_the_comparison_plot_always_draws_the_cited_pairs():
    from app.provenance.plots import plot_result
    rows = [{"key_a": f"d:A:{i}:ALA", "key_b": f"d:A:{i + 100}:GLY", "delta": 0.5 - i * 0.01, "ci_low": 0.4 - i * 0.01,
             "ci_high": 0.6 - i * 0.01, "q": 0.01, "supported": True} for i in range(20)]
    small = {"key_a": "d:A:122:GLY", "key_b": "d:A:118:ASP", "delta": -0.021, "ci_low": -0.03, "ci_high": -0.01, "q": 0.02, "supported": True}
    fig = plot_result({"tool": "compare_intervals", "status": "ok", "provenance_id": "r1", "data_for_ui": {"rows": rows + [small]}}, "ns", focus=["GLY122-ASP118"])
    labels = [t.get_text() for t in fig.axes[0].get_yticklabels()]
    assert any(l.startswith("GLY122-ASP118") for l in labels) and len(labels) == 15
