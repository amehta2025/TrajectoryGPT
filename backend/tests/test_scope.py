"""Scope gate before planning (red-team A1-A4): declined questions get a fixed reply, no plan and no tools."""
import pytest
from fastapi.testclient import TestClient

from app.agent import scope, session
from app.agent.llm import TurnResult, Usage
from app.main import app


class Classifier:
    model, provider = "fake", "meta"

    def __init__(self, verdict):
        self.verdict, self.calls = verdict, 0

    def run_turn(self, items, tools=None, *, schema=None, effort):
        self.calls += 1
        assert schema["name"] == "ScopeVerdict" and effort == "low"
        assert "Do not answer it" in items[0]["content"]
        return TurnResult([], "", {"verdict": self.verdict, "why": "test"}, [],
                          Usage("meta", "fake", effort, 300, 0, 20, 10, 0.5, 0.0001))


@pytest.mark.parametrize("q", ["", "   ", "?", "...", "!!! ???", "1234 5678"])
def test_no_letters_is_declined_without_a_model_call(q):
    llm = Classifier("answerable")
    reply, usages = scope.check(llm, q)
    assert reply["verdict"] == "unclear" and llm.calls == 0 and usages == []
    assert reply["examples"] == scope.EXAMPLES


@pytest.mark.parametrize("verdict", ["unclear", "off_topic", "other_protein", "creative", "needs_context"])
def test_declined_verdicts_get_our_fixed_message(verdict):
    reply, usages = scope.check(Classifier(verdict), "some question")
    assert reply["verdict"] == verdict and reply["message"] == scope.MESSAGES[verdict] and len(usages) == 1


def test_answerable_and_unexpected_replies_go_on_to_planning():
    assert scope.check(Classifier("answerable"), "Which contacts change?")[0] is None
    assert scope.check(Classifier("something else"), "Which contacts change?")[0] is None


def test_needs_context_is_ignored_when_there_is_an_earlier_run():
    assert scope.check(Classifier("needs_context"), "Does that persist?", has_context=True)[0] is None


def test_declined_question_makes_no_plan(monkeypatch):
    monkeypatch.setattr(session, "make_plan", lambda *a, **k: pytest.fail("planner must not run"))
    out = session.propose_plan("Write me a poem", "adk_equilibrium", llm=Classifier("creative"))
    assert out["declined"]["verdict"] == "creative" and "plan_id" not in out
    assert out["usage"][0]["cost_usd"] == 0.0001


def test_plan_endpoint_returns_the_decline(monkeypatch):
    monkeypatch.delenv("DEMO_PASSCODE", raising=False)
    monkeypatch.setattr(session, "new_llm", lambda provider=None: Classifier("off_topic"))
    r = TestClient(app).post("/plan", json={"question": "What's the weather in Chicago?"})
    assert r.status_code == 200 and r.json()["declined"]["verdict"] == "off_topic"


# ---------- Round 2: terse questions about this app are not turned away ----------

@pytest.mark.parametrize("q", ["contacts", "RMSF?", "Explain Q1 and Q2", "ser30?", "How flexible is the LID?"])
@pytest.mark.parametrize("verdict", ["unclear", "needs_context"])
def test_unsure_verdict_on_an_app_question_goes_on_to_planning(q, verdict):
    assert scope.check(Classifier(verdict), q)[0] is None


def test_off_topic_verdicts_still_win_over_app_words():
    assert scope.check(Classifier("other_protein"), "Which contacts change in insulin?")[0]["verdict"] == "other_protein"


@pytest.mark.parametrize("q", ["Does my figure's contact map match yours?", "Reproduce this plot",
                               "Is the attached contact map image right?"])
def test_question_about_the_users_own_figure_asks_for_it_without_a_model_call(q):
    llm = Classifier("answerable")
    reply, usages = scope.check(llm, q)
    assert reply["verdict"] == "needs_figure" and "attached" in reply["message"] and llm.calls == 0
    assert reply["ask_anyway"] is True


def test_unsure_verdict_on_a_figure_question_asks_for_the_figure():
    reply, _ = scope.check(Classifier("needs_context"), "Does the figure match?")
    assert reply["verdict"] == "needs_figure"


def test_figure_words_alone_do_not_need_an_upload():
    assert scope.check(Classifier("answerable"), "Plot the LID opening over time")[0] is None


def test_every_decline_but_no_letters_offers_ask_anyway():
    assert scope.check(Classifier("off_topic"), "weather?")[0]["ask_anyway"] is True
    assert scope.check(Classifier("answerable"), "???")[0]["ask_anyway"] is False


def test_ask_anyway_plans_without_the_gate(monkeypatch):
    monkeypatch.setattr(session, "make_plan", lambda llm, q, ds, ctx=None: ({"dataset": ds, "steps": []}, []))
    llm = Classifier("off_topic")
    out = session.propose_plan("Write me a poem about LID", "adk_transition", llm=llm, skip_scope=True)
    assert "plan_id" in out and llm.calls == 0


def test_skip_scope_reaches_the_planner_over_http(monkeypatch):
    monkeypatch.delenv("DEMO_PASSCODE", raising=False)
    seen = {}
    monkeypatch.setattr(session, "propose_plan", lambda *a, **k: seen.update(k) or {"declined": None})
    TestClient(app).post("/plan", json={"question": "contacts", "skip_scope": True})
    assert seen["skip_scope"] is True
