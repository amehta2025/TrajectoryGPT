"""One LLM interface for the agent: run_turn() over Meta (Responses API) or Baseten (Chat Completions).

History is kept as Responses-API input items (role messages, reasoning, function_call,
function_call_output). The Meta path sends them unchanged; the Baseten path converts them to chat
messages. Retries are ours (SDK max_retries=0): 429/500/503 only, honoring Retry-After, 3 tries.
"""
from __future__ import annotations

import json
import os
import random
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

import openai
from dotenv import load_dotenv

from app.config import REPO_ROOT

load_dotenv(REPO_ROOT / ".env", override=False)

# Reasoning effort per call type (SPEC §9c). Never "none": Muse Spark and Muse Wonder reject it.
EFFORT = {"loop": "low", "plan": "medium", "extract": "medium", "verify": "high"}

# USD per 1M tokens (docs_local/meta/pricing-rate-limits.txt). The one price table for every $ estimate.
# Baseten's Muse Spark Open price is not in our docs: None means "unknown", shown as n/a.
PRICES: dict[str, dict[str, float] | None] = {
    "muse-spark-1.3": {"input": 1.25, "cached": 0.15, "output": 4.25},
    "muse-spark-1.3-contributor": {"input": 0.10, "cached": 0.002, "output": 0.20},
    "muse-wonder-1.0": {"input": 4.00, "cached": 0.20, "output": 20.00},
}

TIMEOUT_S = 120
MAX_TRIES = 3
RETRYABLE = {429, 500, 503}
MAX_OUTPUT_TOKENS = 16000


@dataclass
class Usage:
    provider: str
    model: str
    effort: str
    input_tokens: int = 0
    cached_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    latency_s: float = 0.0
    cost_usd: float | None = None

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class ToolCall:
    call_id: str
    name: str
    arguments: str  # raw JSON text from the model; the tool layer parses and validates it


@dataclass
class TurnResult:
    tool_calls: list[ToolCall]
    final_text: str
    parsed: Any
    output_items: list[dict]  # append to the next turn's input unchanged
    usage: Usage


@dataclass
class CostTracker:
    """Running $ for one run; raises BudgetExceeded once the estimate passes limit_usd."""
    limit_usd: float | None = None
    calls: list[Usage] = field(default_factory=list)

    @property
    def total_usd(self) -> float:
        return sum(u.cost_usd or 0.0 for u in self.calls)

    def add(self, usage: Usage) -> None:
        self.calls.append(usage)
        if self.limit_usd is not None and self.total_usd > self.limit_usd:
            raise BudgetExceeded(f"estimated cost ${self.total_usd:.4f} exceeds the run limit ${self.limit_usd:.2f}")


class BudgetExceeded(RuntimeError):
    pass


def estimate_cost(model: str, input_tokens: int, cached_tokens: int, output_tokens: int) -> float | None:
    """Output tokens include reasoning tokens (billed at the output rate)."""
    p = PRICES.get(model)
    if p is None:
        return None
    uncached = max(0, input_tokens - cached_tokens)
    return (uncached * p["input"] + cached_tokens * p["cached"] + output_tokens * p["output"]) / 1e6


def tool_result_item(call_id: str, output: str) -> dict:
    return {"type": "function_call_output", "call_id": call_id, "output": output}


def _retry_after(err: openai.APIStatusError) -> float | None:
    try:
        return float(err.response.headers.get("retry-after"))
    except (TypeError, ValueError, AttributeError):
        return None


def with_retries(call: Callable[[], Any], sleep: Callable[[float], None] = time.sleep) -> Any:
    """Retry 429/500/503 with exponential backoff + jitter, honoring Retry-After. Never 504 or 4xx."""
    for attempt in range(MAX_TRIES):
        try:
            return call()
        except openai.APIStatusError as e:
            if e.status_code not in RETRYABLE or attempt == MAX_TRIES - 1:
                raise
            delay = 0.5 * 2**attempt + random.random()
            sleep(max(delay, _retry_after(e) or 0.0))


# ---------- Meta: Responses API ----------

def _meta_turn(client, model, input_items, tools, schema, effort) -> tuple[Any, dict]:
    kwargs: dict[str, Any] = dict(
        model=model,
        input=input_items,
        store=False,
        include=["reasoning.encrypted_content"],
        reasoning={"effort": effort},
        max_output_tokens=MAX_OUTPUT_TOKENS,
    )
    if tools:
        kwargs.update(tools=tools, parallel_tool_calls=False)
    if schema is not None:
        kwargs["text"] = {"format": {"type": "json_schema", "name": schema["name"], "schema": schema["schema"], "strict": True}}
    resp = with_retries(lambda: client.responses.create(**kwargs))
    items = [it.model_dump(exclude_none=True) for it in resp.output]
    calls = [ToolCall(it["call_id"], it["name"], it.get("arguments", "")) for it in items if it.get("type") == "function_call"]
    u = resp.usage
    counts = dict(
        input_tokens=u.input_tokens,
        cached_tokens=getattr(u.input_tokens_details, "cached_tokens", 0) or 0,
        output_tokens=u.output_tokens,
        reasoning_tokens=getattr(u.output_tokens_details, "reasoning_tokens", 0) or 0,
    )
    return (calls, resp.output_text or "", items), counts


# ---------- Baseten: Chat Completions ----------

def _content_to_chat(content: Any) -> Any:
    """Responses content parts -> chat content parts (text / image_url)."""
    if isinstance(content, str):
        return content
    parts = []
    for p in content:
        t = p.get("type")
        if t in ("input_text", "output_text", "text"):
            parts.append({"type": "text", "text": p["text"]})
        elif t == "input_image":
            parts.append({"type": "image_url", "image_url": {"url": p["image_url"]}})
    return parts


def to_chat_messages(input_items: list[dict]) -> list[dict]:
    """Convert Responses-style history to chat messages. Reasoning items are dropped (not replayable there)."""
    messages: list[dict] = []
    for it in input_items:
        t = it.get("type", "message")
        if t == "reasoning":
            continue
        if t == "function_call":
            call = {"id": it["call_id"], "type": "function", "function": {"name": it["name"], "arguments": it.get("arguments", "")}}
            if messages and messages[-1]["role"] == "assistant" and "tool_calls" in messages[-1]:
                messages[-1]["tool_calls"].append(call)
            else:
                messages.append({"role": "assistant", "content": "", "tool_calls": [call]})
        elif t == "function_call_output":
            messages.append({"role": "tool", "tool_call_id": it["call_id"], "content": it["output"]})
        else:
            content = _content_to_chat(it["content"])
            if it["role"] == "assistant" and not isinstance(content, str):
                content = "".join(p.get("text", "") for p in content)
            messages.append({"role": it["role"], "content": content})
    return messages


def to_chat_tools(tools: list[dict]) -> list[dict]:
    return [{"type": "function", "function": {k: t[k] for k in ("name", "description", "parameters") if k in t}} for t in tools]


def _baseten_turn(client, model, input_items, tools, schema, effort) -> tuple[Any, dict]:
    kwargs: dict[str, Any] = dict(
        model=model,
        messages=to_chat_messages(input_items),
        reasoning_effort=effort,
        max_tokens=MAX_OUTPUT_TOKENS,
    )
    if tools:
        kwargs.update(tools=to_chat_tools(tools), parallel_tool_calls=False)
    if schema is not None:
        kwargs["response_format"] = {"type": "json_schema", "json_schema": {"name": schema["name"], "schema": schema["schema"]}}
    resp = with_retries(lambda: client.chat.completions.create(**kwargs))
    msg = resp.choices[0].message
    calls = [ToolCall(tc.id, tc.function.name, tc.function.arguments or "") for tc in (msg.tool_calls or [])]
    # Store the assistant turn back in Responses-item form so the history stays one format.
    items: list[dict] = [{"type": "function_call", "call_id": c.call_id, "name": c.name, "arguments": c.arguments} for c in calls]
    if msg.content and not calls:
        items.append({"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": msg.content}]})
    u = resp.usage
    details = getattr(u, "completion_tokens_details", None)
    pdetails = getattr(u, "prompt_tokens_details", None)
    counts = dict(
        input_tokens=u.prompt_tokens,
        cached_tokens=getattr(pdetails, "cached_tokens", 0) or 0,
        output_tokens=u.completion_tokens,
        reasoning_tokens=getattr(details, "reasoning_tokens", 0) or 0,
    )
    return (calls, msg.content or "", items), counts


# ---------- public interface ----------

class LLM:
    def __init__(self, provider: str | None = None, model: str | None = None, client: Any = None,
                 tracker: CostTracker | None = None):
        self.provider = provider or os.environ.get("MODEL_PROVIDER", "meta")
        if self.provider == "meta":
            self.model = model or os.environ.get("META_MODEL", "muse-spark-1.3")
            base_url = os.environ.get("META_API_BASE_URL", "https://api.meta.ai/v1")
            key_name = "META_API_KEY"
        elif self.provider == "baseten":
            self.model = model or os.environ.get("BASETEN_MODEL", "meta-models/muse-spark-open-1026")
            base_url = os.environ.get("BASETEN_BASE_URL", "https://inference.baseten.co/v1")
            key_name = "BASETEN_API_KEY"
        else:
            raise ValueError(f"Unknown MODEL_PROVIDER {self.provider!r}")
        if client is None:
            api_key = os.environ.get(key_name)
            if not api_key:
                raise RuntimeError(f"{key_name} is not set")
            client = openai.OpenAI(base_url=base_url, api_key=api_key, max_retries=0, timeout=TIMEOUT_S)
        self.client = client
        self.tracker = tracker or CostTracker()

    def run_turn(self, input_items: list[dict], tools: list[dict] | None = None, *,
                 schema: dict | None = None, effort: str) -> TurnResult:
        """schema = {"name": ..., "schema": <JSON Schema>} for structured output; then .parsed is the dict."""
        if not effort or effort == "none":
            raise ValueError("reasoning effort must be set explicitly and cannot be 'none'")
        turn = _meta_turn if self.provider == "meta" else _baseten_turn
        t0 = time.perf_counter()
        (calls, text, items), counts = turn(self.client, self.model, input_items, tools or [], schema, effort)
        latency = time.perf_counter() - t0
        usage = Usage(self.provider, self.model, effort, latency_s=round(latency, 2), **counts)
        usage.cost_usd = estimate_cost(self.model, usage.input_tokens, usage.cached_tokens, usage.output_tokens)
        parsed = json.loads(text) if schema is not None and text else None
        result = TurnResult(calls, text, parsed, items, usage)
        self.tracker.add(usage)
        return result
