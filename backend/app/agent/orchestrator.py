"""Turn loop (SPEC §9b). Thin: the model picks every call, all logic lives in tools.run_tool.

Each turn is saved (what the model saw, what it called, the result summary, model vs tool time) to
investigations/<run_id>.json and sent to on_event for the live step log.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from app.agent import prompts
from app.agent.llm import EFFORT, LLM, BudgetExceeded, tool_result_item
from app.agent.tools import ApprovedPlan, RunState, jsonable, run_tool, tools_for
from app.config import REPO_ROOT

MAX_TURNS = 8
MAX_REFUSALS = 2
# Runs are saved here as they progress; on Modal this is on the Volume (/cache/investigations).
INVESTIGATIONS_DIR = Path(os.environ.get("INVESTIGATIONS_DIR") or REPO_ROOT / "investigations")


@dataclass
class Run:
    run_id: str
    question: str
    plan: ApprovedPlan
    state: RunState
    status: str = "running"  # running | needs_review | awaiting_verification | aborted_budget | error
    turns: list[dict] = field(default_factory=list)
    plan_usage: list[dict] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    input_items: list[dict] = field(default_factory=list)
    started: float = field(default_factory=time.perf_counter)
    created: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    wall_s: float = 0.0
    error: str | None = None

    def cost_usd(self) -> float | None:
        costs = [t["usage"]["cost_usd"] for t in self.turns] + [u["cost_usd"] for u in self.plan_usage]
        return None if all(c is None for c in costs) else sum(c or 0.0 for c in costs)

    def as_dict(self) -> dict:
        return jsonable({
            "run_id": self.run_id, "created": self.created, "question": self.question, "status": self.status,
            "plan": self.plan.as_dict(), "checklist": self.state.checklist, "turns": self.turns,
            "plan_usage": self.plan_usage, "results": self.state.results, "findings": self.state.findings,
            "report": self.state.report, "extra_steps": self.state.extra_steps, "problems": self.problems, "wall_s": round(self.wall_s, 2),
            "cost_usd": self.cost_usd(), "error": self.error,
            "input_items": self.input_items,  # full history incl. encrypted reasoning, for resume_run
        })

    def save(self, directory: Path | None = None) -> Path:
        directory = directory or INVESTIGATIONS_DIR
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{self.run_id}.json"
        tmp = path.with_suffix(".json.tmp")  # write then rename, so a reader never sees half a file
        tmp.write_text(json.dumps(self.as_dict(), ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
        return path


def _visible(items: list[dict]) -> list[dict]:
    """Input/output items for the timeline: drop opaque encrypted reasoning, keep everything else."""
    out = []
    for it in items:
        if it.get("type") == "reasoning":
            out.append({"type": "reasoning", "summary": it.get("summary", [])})
        else:
            out.append(it)
    return out


def _auto_check(state: RunState, tool: str, status: str) -> None:
    """Mark the first pending plan item for this tool done when the tool succeeds."""
    if status not in ("ok", "submitted"):
        return
    for item in state.checklist:
        if item["tool"] == tool and item["status"] in ("pending", "in_progress"):
            item["status"] = "done"
            return


def new_run(question: str, plan: ApprovedPlan, run_id: str | None = None) -> Run:
    rid = run_id or datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    return Run(rid, question, plan, RunState(plan))


def run_investigation(
    llm: LLM,
    run: Run,
    *,
    max_turns: int = MAX_TURNS,
    on_event: Callable[[dict], None] | None = None,
    wait_for_next: Callable[[], None] | None = None,
) -> Run:
    emit = on_event or (lambda e: None)
    state, plan = run.state, run.plan
    tools = tools_for(plan.dataset)
    if not run.input_items:
        run.input_items = [{"role": "system", "content": prompts.AGENT_SYSTEM},
                           {"role": "user", "content": prompts.agent_user(run.question, plan.as_dict())}]
    refusals = sum(1 for t in run.turns if t["tool"] == "submit_report" and t["status"] == "refused")
    seen_at_start = 0 if not run.turns else len(run.input_items)  # timeline shows only what is new each turn
    run.save()
    emit({"type": "run_started", "run_id": run.run_id, "checklist": state.checklist})

    try:
        for turn in range(len(run.turns) + 1, max_turns + 1):
            if turn == max_turns:
                run.input_items.append({"role": "user", "content": "Last turn: call submit_report now."})
            new_input = run.input_items[seen_at_start:]
            emit({"type": "turn_started", "turn": turn})
            resp = llm.run_turn(run.input_items, tools, effort=EFFORT["loop"])
            run.input_items += resp.output_items
            seen_at_start = len(run.input_items)
            rec = {"turn": turn, "saw": _visible(new_input), "output": _visible(resp.output_items),
                   "usage": resp.usage.as_dict(), "model_s": resp.usage.latency_s}
            if not resp.tool_calls:
                run.input_items.append({"role": "user", "content": "Call exactly one tool."})
                rec.update(tool=None, status="no_tool_call", tool_s=0.0, summary=resp.final_text[:500])
                run.turns.append(rec)
                emit({"type": "turn", **_event(rec, state)})
                continue
            call = resp.tool_calls[0]
            t0 = time.perf_counter()
            result = run_tool(state, call.name, call.arguments)
            tool_s = round(time.perf_counter() - t0, 2)
            _auto_check(state, call.name, result["status"])
            run.input_items.append(tool_result_item(call.call_id, result["summary_for_model"]))
            rec.update(tool=call.name, arguments=call.arguments, status=result["status"], tool_s=tool_s,
                       provenance_id=result["provenance_id"], summary=result["summary_for_model"])
            run.turns.append(rec)
            run.save()
            emit({"type": "turn", **_event(rec, state), "result": {k: result[k] for k in ("status", "tool", "provenance_id")}})
            if result["status"] == "submitted":
                break
            if call.name == "submit_report" and result["status"] == "refused":
                refusals += 1
                run.problems = json.loads(result["summary_for_model"]).get("problems", [])
                if refusals >= MAX_REFUSALS:
                    break
            if wait_for_next:
                wait_for_next()
        run.status = "awaiting_verification" if state.report else "needs_review"
        if state.report:
            run.problems = []
        elif not run.problems:
            run.problems = [f"no accepted report within {max_turns} turns"]
    except BudgetExceeded as e:
        run.status, run.error = "aborted_budget", str(e)
    except Exception as e:  # an API error ends the run; it is saved and shown, not swallowed
        run.status, run.error = "error", f"{type(e).__name__}: {e}"
    run.wall_s = time.perf_counter() - run.started
    run.save()
    emit({"type": "run_finished", "status": run.status, "report": state.report, "problems": run.problems,
          "cost_usd": run.cost_usd(), "wall_s": round(run.wall_s, 2), "error": run.error})
    return run


def _event(rec: dict, state: RunState) -> dict:
    done = sum(c["status"] in ("done", "skipped") for c in state.checklist)
    return {"turn": rec["turn"], "tool": rec["tool"], "status": rec["status"], "model_s": rec["model_s"],
            "tool_s": rec["tool_s"], "cost_usd": rec["usage"]["cost_usd"], "effort": rec["usage"]["effort"],
            "provenance_id": rec.get("provenance_id"), "checklist": state.checklist,
            "progress": f"{done}/{len(state.checklist)} done"}


def load_run(run_id: str, directory: Path | None = None) -> Run:
    """Rebuild a saved run so resume_run can continue it."""
    d = json.loads(((directory or INVESTIGATIONS_DIR) / f"{run_id}.json").read_text(encoding="utf-8"))
    plan = ApprovedPlan(**d["plan"])
    state = RunState(plan, results=d["results"], findings=d["findings"], checklist=d["checklist"], report=d["report"],
                     extra_steps=d.get("extra_steps", []))
    return Run(d["run_id"], d["question"], plan, state, status=d["status"], turns=d["turns"],
               plan_usage=d["plan_usage"], problems=d["problems"], input_items=d["input_items"], created=d["created"],
               wall_s=d.get("wall_s", 0.0), error=d.get("error"))


def list_runs(limit: int = 50, directory: Path | None = None) -> list[dict]:
    """Saved runs, newest first: id, created, question, dataset, status, cost (for curation and replay)."""
    d = directory or INVESTIGATIONS_DIR
    out = []
    for p in sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
        try:
            r = json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            continue
        out.append({k: r.get(k) for k in ("run_id", "created", "question", "status", "cost_usd", "wall_s")}
                   | {"dataset": r["plan"]["dataset"]})
    return out


def resume_run(llm: LLM, run_id: str, **kwargs) -> Run:
    return run_investigation(llm, load_run(run_id), **kwargs)
