"""Server-side state for the UI: proposed plans, running investigations, and their event streams.

Plans are stored here when proposed; a run starts only from a stored plan plus the user's edits, which
are validated again in code (SPEC §9 step 4). Each run executes in a background thread and appends
events to a list that the SSE endpoint streams (late subscribers replay from the start).
"""
from __future__ import annotations

import os
import threading
import uuid
from dataclasses import dataclass, field

from app.agent.llm import LLM, CostTracker
from app.agent import orchestrator
from app.agent.orchestrator import Run, new_run, run_investigation
from app.agent.planner import make_plan, to_approved
from app.safety import budget

RUN_BUDGET_USD = float(os.environ.get("RUN_BUDGET_USD", "0.50"))
EDITABLE = ("cutoff", "robustness_cutoffs", "window_a", "window_b", "stride", "sequence_exclusion", "group_a", "group_b")


@dataclass
class RunHandle:
    run: Run
    step_through: bool = False
    events: list[dict] = field(default_factory=list)
    cond: threading.Condition = field(default_factory=threading.Condition)
    go: threading.Event = field(default_factory=threading.Event)
    done: bool = False

    def emit(self, e: dict) -> None:
        with self.cond:
            self.events.append({"seq": len(self.events), **e})
            if e["type"] == "run_finished":
                self.done = True
            self.cond.notify_all()

    def wait_for_next(self) -> None:
        """Step-through: pause after a turn until the UI's Next (or step-through is switched off)."""
        if not self.step_through:
            return
        self.emit({"type": "paused"})
        self.go.wait()
        self.go.clear()

    def stream(self, after: int = 0, timeout: float = 15.0):
        """Yield events with seq >= after; a None is a keep-alive when nothing arrived within timeout."""
        i = after
        while True:
            with self.cond:
                if i >= len(self.events) and not self.done:
                    self.cond.wait(timeout)
                batch = self.events[i:]
                finished = self.done
            if not batch:
                if finished:
                    return
                yield None
                continue
            for e in batch:
                yield e
            i += len(batch)
            if finished and i >= len(self.events):
                return


PLANS: dict[str, dict] = {}
ON_RUN_FINISHED = None  # set on Modal to commit the Volume, so a finished run survives restarts
RUNS: dict[str, RunHandle] = {}
_lock = threading.Lock()


def propose_plan(question: str, dataset: str, llm: LLM | None = None) -> dict:
    llm = llm or LLM(tracker=CostTracker(limit_usd=RUN_BUDGET_USD, on_usage=budget.record))
    plan, usages = make_plan(llm, question, dataset)
    pid = uuid.uuid4().hex[:12]
    with _lock:
        PLANS[pid] = {"question": question, "dataset": dataset, "plan": plan, "usage": [u.as_dict() for u in usages],
                      "llm": llm}
    return {"plan_id": pid, "plan": plan, "usage": PLANS[pid]["usage"], "model": llm.model}


def propose_figure_plan(files: list[dict], dataset: str, note: str = "", llm: LLM | None = None) -> dict:
    """Figure mode: uploads -> extracted spec -> plan, stored like a question's plan."""
    from app.agent.figure import plan_from_figure
    llm = llm or LLM(tracker=CostTracker(limit_usd=RUN_BUDGET_USD, on_usage=budget.record))
    out = plan_from_figure(llm, files, dataset, note)
    pid = uuid.uuid4().hex[:12]
    usage = [u.as_dict() for u in out["usages"]]
    with _lock:
        PLANS[pid] = {"question": out["question"], "dataset": out["plan"]["dataset"], "plan": out["plan"],
                      "usage": usage, "llm": llm, "figure": out["figure"]}
    return {"plan_id": pid, "plan": out["plan"], "usage": usage, "model": llm.model, "figure": out["figure"],
            "question": out["question"]}


def start_run(plan_id: str, edits: dict, step_through: bool = False) -> str:
    """Approve the stored plan with the user's edits (validated in code) and start it in the background."""
    with _lock:
        stored = PLANS.get(plan_id)
    if stored is None:
        raise KeyError(f"unknown plan {plan_id}")
    plan = {**stored["plan"], **{k: v for k, v in edits.items() if k in EDITABLE}}
    if "steps" in edits:  # the user may drop steps, never add tools the planner did not propose
        proposed = {s["tool"] for s in stored["plan"]["steps"]}
        plan["steps"] = [s for s in edits["steps"] if s.get("tool") in proposed]
    approved = to_approved(plan)  # raises PlanInvalid with the problems
    run = new_run(stored["question"], approved)
    run.plan_usage = stored["usage"]
    run.figure = stored.get("figure")
    handle = RunHandle(run, step_through=step_through)
    with _lock:
        RUNS[run.run_id] = handle
    llm: LLM = stored["llm"]

    def work():
        run_investigation(llm, run, on_event=handle.emit, wait_for_next=handle.wait_for_next)
        if ON_RUN_FINISHED:
            ON_RUN_FINISHED()

    threading.Thread(target=work, name=f"run-{run.run_id}", daemon=True).start()
    return run.run_id


def get_handle(run_id: str) -> RunHandle:
    with _lock:
        h = RUNS.get(run_id)
    if h is None:
        raise KeyError(f"unknown run {run_id}")
    return h


def next_turn(run_id: str, step_through: bool | None = None) -> None:
    """Next (step_through None) releases a paused turn; the toggle only sets the mode, and switching it
    off also releases a pause so the run continues."""
    h = get_handle(run_id)
    if step_through is not None:
        h.step_through = step_through
    if not step_through:
        h.go.set()


def run_view(run_id: str) -> dict:
    """Everything the UI shows for a run: plan, checklist, timeline, results, findings, report.
    A run this container is not running is read back from its saved file (e.g. after a restart)."""
    with _lock:
        h = RUNS.get(run_id)
    if h is not None:
        d, step = h.run.as_dict(), h.step_through
    else:
        try:
            d, step = orchestrator.load_run(run_id).as_dict(), False
        except (FileNotFoundError, ValueError):
            raise KeyError(f"unknown run {run_id}") from None
    d.pop("input_items")
    d["step_through"] = step
    return d
