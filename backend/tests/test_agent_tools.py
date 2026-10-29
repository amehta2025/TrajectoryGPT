"""Agent tools, guardrails, placeholders and the deterministic check, on adk_transition (no LLM)."""
import json

import pytest

import app.agent.report as report
import app.agent.tools as tools
import app.science.contact_stats as cs
from app.agent.report import bare_numbers, format_value
from app.agent.tools import ApprovedPlan, RunState, run_tool, tools_for
from app.config import dataset_config

DS = "adk_transition"
STEPS = [{"tool": t, "rationale": ""} for t in ("calculate_contacts", "compare_intervals", "contact_statistics",
                                                 "cutoff_sensitivity", "submit_report")]


def new_state(steps=STEPS) -> RunState:
    return RunState(ApprovedPlan(DS, "NMP", "CORE", 4.5, [0, 49], [49, 98], steps=steps))


@pytest.fixture
def pretend_stats_valid(monkeypatch):
    """The transition set refuses stats; pretend it doesn't, to exercise the stats tools quickly."""
    real = dataset_config(DS)
    fake = lambda ds: {**real, "stats_valid": True}
    for mod in (tools, cs, report):
        monkeypatch.setattr(mod, "dataset_config", fake)


def call(state, name, **args):
    return run_tool(state, name, json.dumps(args))


def supported_pair(state):
    """No transition pair is supported at 4.5 Å (too few chunks), so mark the largest change as supported
    in the stored result: the check reads only what is stored."""
    r = call(state, "compare_intervals")
    ranked = sorted(r["pairs"], key=lambda p: -abs(r["pairs"][p]["delta"]))
    r["pairs"][ranked[0]]["supported"] = True
    return r["provenance_id"], ranked[0], ranked[-1]


# ---------- guardrails ----------

def test_stats_tools_hidden_and_refused_on_transition():
    names = {t["name"] for t in tools_for(DS)}
    assert "compare_intervals" not in names and "calculate_contacts" in names
    r = call(new_state(), "compare_intervals")
    assert r["status"] == "refused" and "biased" in r["summary_for_model"]


@pytest.mark.parametrize("args", [{"dataset": "adk_equilibrium"}, {"cutoff": 6.0}, {"group_a": "LID"}, {"start": 0}])
def test_approved_params_cannot_change(args):
    r = run_tool(new_state(), "calculate_contacts", json.dumps(args))
    assert r["status"] == "refused"


def test_only_one_extra_step():
    s = new_state([{"tool": "calculate_contacts"}])
    assert call(s, "distance_over_time", pair="LYS57-GLU170")["status"] == "ok"  # the one extra step
    assert call(s, "distance_over_time", pair="ARG36-ASP84")["status"] == "ok"   # same extra tool again
    r = call(s, "native_contacts")
    assert r["status"] == "refused" and "one extra step" in r["summary_for_model"]
    assert call(s, "get_metadata")["status"] == "ok"  # informational tools are free


def test_pairs_must_be_in_approved_groups():
    s = new_state()
    assert call(s, "distance_over_time", pair="LYS157-ARG36")["status"] == "refused"  # LID-NMP, not approved
    assert call(s, "distance_over_time", pair="ALA36-GLU170")["status"] == "refused"  # wrong resname
    r = call(s, "distance_over_time", pair="GLU170-LYS57")  # reversed order is fine
    assert r["status"] == "ok" and "LYS57-GLU170" in r["pairs"]


def test_bad_json_is_an_error_not_a_crash():
    assert run_tool(new_state(), "calculate_contacts", "{not json")["status"] == "error"


def test_summaries_are_short(pretend_stats_valid):
    s = new_state()
    for name, args in (("calculate_contacts", {}), ("compare_intervals", {}), ("cutoff_sensitivity", {})):
        r = call(s, name, **args)
        assert r["status"] == "ok"
        assert len(r["summary_for_model"]) / 4 < 2000  # ~tokens


# ---------- placeholders and the deterministic check ----------

def test_format_value():
    assert format_value(-0.1274, "delta") == "−0.127"
    assert format_value(0.0004, "q") == "4.0e−04"
    assert format_value(0.31, "q") == "0.31"
    assert format_value([-0.185, -0.07], "ci") == "[−0.185, −0.070]"
    assert format_value([4.0, 4.5], "cutoff_list") == "4.0, 4.5 Å"
    assert format_value(12.34, "tau_ns") == "12.3 ns"


@pytest.mark.parametrize("text,bad", [
    ("HSE134 and ASP118 lose contact", []),
    ("residues 122–159 form the LID", []),
    ("at the 4.5 Å cutoff over frames 0–49", []),
    ("frequency fell from 0.53 to 0.40", ["0.53", "0.40"]),
    ("29 pairs were tested", ["29"]),
    ("{{r2.n_tested}} pairs were tested", []),
])
def test_bare_numbers(text, bad):
    assert bare_numbers(new_state(), text) == bad


def test_record_finding_checks_evidence_and_placeholders():
    s = new_state()
    pid = call(s, "calculate_contacts")["provenance_id"]
    assert call(s, "record_finding", text="x", evidence_ids=["r9"], claim="descriptive")["status"] == "refused"
    r = call(s, "record_finding", text="{{r1.nope}}", evidence_ids=[pid], claim="descriptive")
    assert r["status"] == "refused" and "does not resolve" in r["summary_for_model"]
    r = call(s, "record_finding", text=f"{{{{{pid}.n_pairs_in_contact}}}} pairs touch", evidence_ids=[pid], claim="descriptive")
    assert r["status"] == "ok"


def test_submit_full_check(pretend_stats_valid):
    s = new_state()
    pid, sup, uns = supported_pair(s)

    def finding(text, ev, claim):
        return json.loads(call(s, "record_finding", text=text, evidence_ids=ev, claim=claim)["summary_for_model"])["finding_id"]

    f_bad = finding(f"{uns} changed, Δ = {{{{{pid}.{uns}.delta}}}}", [f"{pid}.{uns}"], "supported")
    r = call(s, "submit_report", finding_ids=[f_bad])
    assert r["status"] == "refused" and "is not supported" in r["summary_for_model"]

    f_words = finding(f"{uns} is significantly different", [f"{pid}.{uns}"], "descriptive")
    assert "without claim='supported'" in call(s, "submit_report", finding_ids=[f_words])["summary_for_model"]

    f_null = finding(f"{uns} is not supported (q = {{{{{pid}.{uns}.q}}}})", [f"{pid}.{uns}"], "not_supported")
    f_ok = finding(f"{sup} changes by {{{{{pid}.{sup}.delta}}}}, CI {{{{{pid}.{sup}.ci}}}}, supported",
                   [f"{pid}.{sup}"], "supported")
    r = call(s, "submit_report", finding_ids=[f_ok, f_null])
    assert r["status"] == "refused" and "run cutoff_sensitivity" in r["summary_for_model"]

    rob = call(s, "cutoff_sensitivity", pairs=[sup])
    verdict = rob["pairs"][sup]["verdict"]
    if verdict != "robust":
        f_rob = finding(f"{sup} is a robust supported change", [f"{pid}.{sup}"], "supported")
        assert "no cutoff_sensitivity result shows it robust" in call(s, "submit_report", finding_ids=[f_rob])["summary_for_model"]

    r = call(s, "submit_report", finding_ids=[f_ok, f_null], gaps=["0.5 of the run is unexplained"])
    assert r["status"] == "refused" and "gaps[0]: bare numbers" in r["summary_for_model"]

    r = call(s, "submit_report", finding_ids=[f_ok, f_null], hypotheses=["LID motion may loosen this contact"])
    assert r["status"] == "submitted"
    rep = s.report
    f0 = rep["findings"][0]
    assert "{{" not in f0["rendered"] and f0["badge"] == "proven" and f0["spans"][0]["pid"] == pid
    assert f0["robustness"][0]["verdict"] == verdict
    assert rep["findings"][1]["badge"] == "unsupported"
    assert rep["statistical_support"] and len(rep["caveats"]) >= 2
