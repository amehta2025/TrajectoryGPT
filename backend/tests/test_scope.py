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
