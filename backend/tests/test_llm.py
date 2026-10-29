"""LLM wrapper without the network: retries, request shape, history conversion, cost."""
from types import SimpleNamespace as NS

import httpx
import openai
import pytest

from app.agent import llm
from app.agent.llm import LLM, BudgetExceeded, CostTracker, Usage, estimate_cost, to_chat_messages, with_retries


def status_error(code: int, retry_after: str | None = None) -> openai.APIStatusError:
    headers = {"retry-after": retry_after} if retry_after else {}
    resp = httpx.Response(code, headers=headers, request=httpx.Request("POST", "https://x/v1/responses"))
    return openai.APIStatusError(f"HTTP {code}", response=resp, body=None)


def flaky(errors, value="ok"):
    calls = []

    def call():
        calls.append(1)
        if len(calls) <= len(errors):
            raise errors[len(calls) - 1]
        return value
    return call, calls


def test_retries_429_and_honors_retry_after():
    sleeps = []
    call, calls = flaky([status_error(429, "7"), status_error(503)])
    assert with_retries(call, sleep=sleeps.append) == "ok"
    assert len(calls) == 3
    assert sleeps[0] >= 7  # Retry-After honored


@pytest.mark.parametrize("code", [400, 401, 404, 413, 504])
def test_never_retries_4xx_or_504(code):
    call, calls = flaky([status_error(code)])
    with pytest.raises(openai.APIStatusError):
        with_retries(call, sleep=lambda s: None)
    assert len(calls) == 1


def test_gives_up_after_three_tries():
    call, calls = flaky([status_error(500)] * 5)
    with pytest.raises(openai.APIStatusError):
        with_retries(call, sleep=lambda s: None)
    assert len(calls) == 3


class FakeResponses:
    def __init__(self):
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        item = NS(model_dump=lambda exclude_none=True: {
            "type": "function_call", "call_id": "c1", "name": "get_metadata", "arguments": "{}"})
        usage = NS(input_tokens=1000, output_tokens=100, input_tokens_details=NS(cached_tokens=400),
                   output_tokens_details=NS(reasoning_tokens=60))
        return NS(output=[item], output_text="", usage=usage)


def test_meta_request_shape_and_usage():
    fake = FakeResponses()
    model = LLM("meta", model="muse-spark-1.3", client=NS(responses=fake))
    r = model.run_turn([{"role": "user", "content": "hi"}], [{"type": "function", "name": "get_metadata"}], effort="low")
    k = fake.kwargs
    assert k["store"] is False
    assert k["include"] == ["reasoning.encrypted_content"]
    assert k["reasoning"] == {"effort": "low"}
    assert k["parallel_tool_calls"] is False
    assert r.tool_calls[0].name == "get_metadata"
    assert r.output_items[0]["call_id"] == "c1"
    assert r.usage.cached_tokens == 400 and r.usage.reasoning_tokens == 60
    assert r.usage.cost_usd == pytest.approx((600 * 1.25 + 400 * 0.15 + 100 * 4.25) / 1e6)


def test_effort_must_be_explicit():
    model = LLM("meta", model="muse-spark-1.3", client=NS(responses=FakeResponses()))
    for bad in ("", "none"):
        with pytest.raises(ValueError):
            model.run_turn([], effort=bad)


def test_history_converts_to_chat_messages():
    items = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": [{"type": "input_text", "text": "q"}, {"type": "input_image", "image_url": "data:x"}]},
        {"type": "reasoning", "id": "rs_1", "encrypted_content": "zzz"},
        {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "thinking"}], "phase": "commentary"},
        {"type": "function_call", "call_id": "c1", "name": "t", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "c1", "output": "res"},
    ]
    msgs = to_chat_messages(items)
    assert [m["role"] for m in msgs] == ["system", "user", "assistant", "assistant", "tool"]
    assert msgs[1]["content"][1] == {"type": "image_url", "image_url": {"url": "data:x"}}
    assert msgs[3]["tool_calls"][0]["id"] == "c1"
    assert msgs[4] == {"role": "tool", "tool_call_id": "c1", "content": "res"}


def test_cost_unknown_model_is_none():
    assert estimate_cost("meta-models/muse-spark-open-1026", 100, 0, 100) is None


def test_budget_guard_aborts():
    t = CostTracker(limit_usd=0.50)
    t.add(Usage("meta", "m", "low", cost_usd=0.30))
    with pytest.raises(BudgetExceeded):
        t.add(Usage("meta", "m", "low", cost_usd=0.25))


def test_effort_table_never_none():
    assert "none" not in llm.EFFORT.values()
