"""Follow-ups with session state (SPEC §9 step 8) and suggested next questions, without an LLM."""
import json

import pytest

import app.agent.orchestrator as orch
from app.agent import prompts, report, session
from tests.test_verifier import run  # noqa: F401  (fixture: a run with an accepted report)


class _Fake:
    model = "fake"
    provider = "fake"


FAKE_LLM = _Fake()


@pytest.fixture(autouse=True)
def clean(tmp_path, monkeypatch):
    monkeypatch.setattr(session.scope, "check", lambda llm, q, has_context=False: (None, []))  # tested in test_scope.py
    monkeypatch.setattr(orch, "INVESTIGATIONS_DIR", tmp_path)
    monkeypatch.setattr(session, "SESSIONS", {})
    monkeypatch.setattr(session, "PLANS", {})


def _supported(run):
    rows = run.state.results["r1"]["data_for_ui"]["rows"]
    rows[1].update(supported=True, q=0.002)  # SER30-ASP118 becomes the top supported pair
    return run


def test_focus_pair_prefers_the_selection_then_the_top_supported_pair(run):
    _supported(run)
    assert session.focus_pair(run, "SER30-ASP84") == ("SER30-ASP84", "the pair the user selected")
    assert session.focus_pair(run, None) == ("SER30-ASP118", "the top supported pair")
    assert session.focus_pair(run, "ALA1-GLY2")[0] == "SER30-ASP118"  # not in this run: ignored


def test_follow_up_plans_from_the_sessions_last_run(run, monkeypatch):
    _supported(run).save()
    seen = {}

    def fake_make_plan(llm, question, dataset, context=None):
        seen.update(question=question, dataset=dataset, context=context)
        return {"dataset": dataset, "steps": []}, []

    monkeypatch.setattr(session, "make_plan", fake_make_plan)
    session.remember("sess-12345678", last_run_id=run.run_id)
    out = session.propose_plan("Does that persist?", "adk_equilibrium", llm=FAKE_LLM, session_id="sess-12345678",
                               selected_pair=None, follow_up=True)
    ctx = seen["context"]
    assert seen["dataset"] == "adk_transition"  # stays on the previous run's dataset
    assert ctx["focus_pair"] == "SER30-ASP118" and ctx["question"] == "Which contacts change?"
    assert out["follow_up"] == {"run_id": run.run_id, "question": "Which contacts change?", "focus_pair": "SER30-ASP118"}
    block = prompts.follow_up_block(ctx)
    assert "SER30-ASP118" in block and "window_b [49, 98]" in block and "Keep the previous dataset" in block
    # A fresh question in the same session gets no context.
    session.propose_plan("Something new", "adk_equilibrium", llm=FAKE_LLM, session_id="sess-12345678")
    assert seen["context"] is None and seen["dataset"] == "adk_equilibrium"


def test_selected_pair_is_stored_and_used(run, monkeypatch):
    _supported(run).save()
    monkeypatch.setattr(session, "make_plan", lambda llm, q, ds, context=None: ({"context": context}, []))
    session.remember("sess-abcdefgh", last_run_id=run.run_id)
    out = session.propose_plan("Does that persist?", "adk_transition", llm=FAKE_LLM, session_id="sess-abcdefgh",
                               selected_pair="SER30-ASP84", follow_up=True)
    assert out["plan"]["context"]["focus_pair"] == "SER30-ASP84"
    assert session.SESSIONS["sess-abcdefgh"]["selected_pair"] == "SER30-ASP84"


def test_sessions_are_capped(monkeypatch):
    monkeypatch.setattr(session, "MAX_SESSIONS", 3)
    for i in range(5):
        session.remember(f"s{i}", last_run_id=str(i))
    assert list(session.SESSIONS) == ["s2", "s3", "s4"]


def test_only_two_next_questions_are_considered(run):
    kept, problems = report.next_questions(run.state, {"next_questions": [
        "Why", "{{r1.n_tested}} pairs?", "Does the SER30-ASP84 drop persist at 5.0 Å?"]})
    assert kept == [] and problems == ["next_questions[0]: must be 10-200 characters",
                                       "next_questions[1]: cannot contain placeholders"]


def test_next_questions_allow_residue_numbers_and_approved_cutoffs(run):
    kept, problems = report.next_questions(run.state, {"next_questions": [
        "Does the SER30-ASP84 drop persist at 4.5 Å?", "Is the 31% drop real?"]})
    assert kept == ["Does the SER30-ASP84 drop persist at 4.5 Å?"] and "bare numbers" in problems[0]
    rep = report.build_report(run.state, {"finding_ids": ["f1"], "next_questions": ["What happens in the second half?"]})
    assert rep["next_questions"] == ["What happens in the second half?"]
