"""Honesty checks from the red-team pass: data notes for what the data cannot give (A6, A7), no causal or
linking wording in findings or the headline (A11). "Does that persist?" with no prior run is in test_scope.py."""
import json

import pytest

from app.agent import limits, report
from app.agent.planner import to_approved
from app.agent.tools import RunState, run_tool
from app.config import dataset_config

DS = "adk_transition"


@pytest.fixture
def timed(monkeypatch):
    """Pretend the transition path has 10 ns per frame (98 frames = 0.98 µs) to test the time note quickly."""
    real = dataset_config(DS)
    monkeypatch.setattr(limits, "dataset_config", lambda ds: {**real, "dt_ns": 10.0})


def test_time_range_longer_than_the_run_gets_a_code_written_note(timed):
    notes = limits.data_notes("Compare the first 2 µs to the last 2 µs", DS)
    assert notes == ["You asked about 2.00 µs, but this run covers only 980 ns (98 frames), so the analysis "
                     "can only use the run as it is."]
    assert limits.data_notes("Compare the first 300 ns to the last 300 ns", DS) == []
    assert limits.data_notes("What happens in the first 1500 ns?", DS)  # ns works too


def test_time_on_a_path_without_time_says_path_steps():
    assert "path steps" in limits.data_notes("Compare the first 2 us to the last 2 us", DS)[0]


def test_residue_that_does_not_exist_or_has_another_name():
    assert limits.data_notes("Does residue 500 touch residue 3?", DS) == [
        "There is no residue 500: this protein has residues 1–214."]
    assert limits.data_notes("Does TRP999 touch LID?", DS) == ["There is no residue 999: this protein has residues 1–214."]
    assert limits.data_notes("Does LYS30 touch ASP84?", DS) == ["Residue 30 is SER, not LYS; the analysis uses SER30."]
    assert limits.data_notes("Does HIS134 touch ASP118?", DS) == []  # CHARMM HSE134 is a histidine
    assert limits.data_notes("Which LID-CORE contacts change? Is SER30-ASP84 real?", DS) == []


def test_notes_ride_on_the_approved_plan_into_the_report_caveats():
    plan = {"dataset": DS, "group_a": "NMP", "group_b": "CORE", "cutoff": 4.5, "window_a": [0, 98], "window_b": None,
            "stride": 1, "sequence_exclusion": 2, "robustness_cutoffs": [4.0, 4.5, 5.0],
            "steps": [{"tool": "calculate_contacts", "rationale": ""}, {"tool": "submit_report", "rationale": ""}],
            "notes": ["Residue 30 is SER, not LYS; the analysis uses SER30."]}
    s = RunState(to_approved(plan))
    pid = json.loads(run_tool(s, "calculate_contacts", "{}")["summary_for_model"])["provenance_id"]
    run_tool(s, "record_finding", json.dumps({"text": "They touch in {{%s.any_contact_freq}} of frames." % pid,
                                              "evidence_ids": [pid], "claim": "descriptive"}))
    r = run_tool(s, "submit_report", json.dumps({"finding_ids": ["f1"]}))
    assert r["status"] == "submitted"
    assert s.report["caveats"][0]["text"] == "Residue 30 is SER, not LYS; the analysis uses SER30."


@pytest.mark.parametrize("text", ["LID opening is linked to loss of LID-CORE contacts",
                                  "The drop is caused by LID opening", "This drives the opening",
                                  "The change is due to solvent", "This explains the shift"])
def test_causal_wording_is_found(text):
    assert report.causal_words(text)


def test_measurement_wording_is_not_causal():
    for text in ["This contact formed less often in the second half.", "The lid stays open through the run.",
                 "None of the 53 tested pairs changed."]:
        assert report.causal_words(text) == []


def test_causal_headline_is_dropped_and_causal_finding_refused():
    plan = to_approved({"dataset": DS, "group_a": "LID", "group_b": "CORE", "cutoff": 4.5, "window_a": [0, 98],
                        "window_b": None, "stride": 1, "sequence_exclusion": 2, "robustness_cutoffs": [4.0, 4.5, 5.0],
                        "steps": [{"tool": "calculate_contacts", "rationale": ""}, {"tool": "submit_report", "rationale": ""}]})
    s = RunState(plan)
    pid = json.loads(run_tool(s, "calculate_contacts", "{}")["summary_for_model"])["provenance_id"]
    run_tool(s, "record_finding", json.dumps({"text": "LID opening is caused by contacts touching in {{%s.any_contact_freq}} of frames." % pid,
                                              "evidence_ids": [pid], "claim": "descriptive"}))
    r = run_tool(s, "submit_report", json.dumps({"finding_ids": ["f1"]}))
    assert r["status"] == "refused" and "causal or linking wording" in r["summary_for_model"]
    run_tool(s, "record_finding", json.dumps({"text": "LID and CORE touch in {{%s.any_contact_freq}} of frames." % pid,
                                              "evidence_ids": [pid], "claim": "descriptive"}))
    r = run_tool(s, "submit_report", json.dumps({"finding_ids": ["f2"],
                                                 "headline": "LID opening is linked to these contacts."}))
    assert r["status"] == "submitted" and s.report["headline"] is None
    assert any("causal" in p for p in s.report["text_dropped"])


# ---------- Round 2: a residue that doesn't match is never guessed ----------

@pytest.mark.parametrize("q, first_choice, as_numbered", [
    ("Does HSE143 touch ASP118?", "Does HSD134 touch ASP118?", "Does GLU143 touch ASP118?"),  # HSD on this dataset
    ("Is ASP48 near LID?", "Is ASP84 near LID?", "Is GLN48 near LID?"),
    ("lys30 contacts", "LYS40 contacts", "SER30 contacts"),
])
def test_mismatched_residue_offers_choices(q, first_choice, as_numbered):
    out = limits.residue_check(q, DS)
    questions = [c["question"] for c in out["choices"]]
    assert out["verdict"] == "check_residue" and questions[0] == first_choice and questions[-1] == as_numbered


def test_matching_residues_need_no_check():
    for q in ["Does HSE134 touch ASP118?", "HIS134 and asp-118", "ser30-asp84", "we met 3 times", "Explain Q1 and Q2"]:
        assert limits.residue_check(q, DS) is None, q


def test_residue_out_of_range_says_the_range():
    out = limits.residue_check("Does TRP999 move?", DS)
    assert out["message"].startswith("There is no residue 999: this protein has residues 1–214.")


def test_near_numbers_tries_swapped_digits_first():
    assert limits.near_numbers(143)[:1] == [134] and 143 not in limits.near_numbers(143)


def test_plan_stops_at_a_mismatched_residue_before_any_model_call(monkeypatch):
    from app.agent import session

    class NoCalls:
        model, provider = "fake", "meta"

        def run_turn(self, *a, **k):
            pytest.fail("no model call before the user picks a residue")

    out = session.propose_plan("Does HSE143 touch ASP118?", DS, llm=NoCalls())
    assert out["declined"]["verdict"] == "check_residue" and "plan_id" not in out
