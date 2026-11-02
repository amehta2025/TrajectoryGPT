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
from app.agent import orchestrator, scope, verifier
from app.agent.orchestrator import Run, new_run, run_investigation
from app.agent.planner import PlanInvalid, make_plan, to_approved
from app.agent.tools import res_label
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
            # The stream ends with the run, or after the background verifier when a report was accepted.
            if e["type"] == "verification_finished" or (e["type"] == "run_finished" and e["status"] != "awaiting_verification"):
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
# Per browser session (SPEC §9 step 8): last run, its approved params, the selected pair. In memory: one container.
SESSIONS: dict[str, dict] = {}
MAX_SESSIONS = 2000
ON_RUN_FINISHED = None  # set on Modal to commit the Volume, so a finished run survives restarts
RUNS: dict[str, RunHandle] = {}
_lock = threading.Lock()


def remember(session_id: str | None, **fields) -> None:
    if not session_id:
        return
    with _lock:
        s = SESSIONS.pop(session_id, {})
        SESSIONS[session_id] = {**s, **fields}  # re-inserted last: the oldest session is dropped first
        while len(SESSIONS) > MAX_SESSIONS:
            SESSIONS.pop(next(iter(SESSIONS)))


def focus_pair(run: Run, selected: str | None) -> tuple[str | None, str]:
    """What "that" refers to: the pair the user selected, else the top supported pair (lowest q)."""
    labels = {pair for r in run.state.results.values() for pair in r.get("pairs", {})}
    if selected and selected in labels:
        return selected, "the pair the user selected"
    best = None
    for r in run.state.results.values():
        if r["tool"] == "compare_intervals" and r["status"] == "ok":
            for row in (r["data_for_ui"] or {}).get("rows", []):
                if row["supported"] and (best is None or row["q"] < best[1]):
                    best = (f"{res_label(row['key_a'])}-{res_label(row['key_b'])}", row["q"])
    return (best[0], "the top supported pair") if best else (None, "")


def follow_up_context(session_id: str | None) -> dict | None:
    """The previous run of this session, for the planner; None when there is none (a fresh question)."""
    with _lock:
        s = dict(SESSIONS.get(session_id or "", {}))
    rid = s.get("last_run_id")
    if not rid:
        return None
    try:
        run = get_handle(rid).run
    except KeyError:
        try:
            run = orchestrator.load_run(rid)
        except (FileNotFoundError, ValueError):
            return None
    if not run.state.report:
        return None
    pair, why = focus_pair(run, s.get("selected_pair"))
    findings = [(f.get("plain") or {}).get("text") or f["rendered"] for f in run.state.report["findings"]][:3]
    return {"run_id": rid, "question": run.question, "plan": run.plan.as_dict(), "findings": findings,
            "focus_pair": pair, "focus_reason": why}


def new_llm(provider: str | None = None) -> LLM:
    """The agent's model: Meta (Muse Spark) by default, or Baseten (Muse Spark Open, open weights).
    The plan stores this client, so the run uses the same provider; the verifier is always Muse Wonder."""
    return LLM(provider, tracker=CostTracker(limit_usd=RUN_BUDGET_USD, on_usage=budget.record))


def propose_plan(question: str, dataset: str, llm: LLM | None = None, session_id: str | None = None,
                 selected_pair: str | None = None, follow_up: bool = False, provider: str | None = None) -> dict:
    """A follow-up uses the session's last run (its params, findings, and the pair "that" refers to)."""
    llm = llm or new_llm(provider)
    if selected_pair is not None:
        remember(session_id, selected_pair=selected_pair)
    context = follow_up_context(session_id) if follow_up else None
    dataset = context["plan"]["dataset"] if context else dataset  # a follow-up stays on the previous dataset
    gate, gate_usages = scope.check(llm, question, has_context=context is not None)
    if gate:  # nonsense, off-topic, other protein, creative, or "that" with nothing before it: no plan, no tools
        return {"declined": gate, "usage": [u.as_dict() for u in gate_usages], "model": llm.model,
                "provider": llm.provider}
    plan, usages = make_plan(llm, question, dataset, context)
    usages = gate_usages + usages
    pid = uuid.uuid4().hex[:12]
    with _lock:
        PLANS[pid] = {"question": question, "dataset": dataset, "plan": plan, "usage": [u.as_dict() for u in usages],
                      "llm": llm, "session_id": session_id}
    follow = {k: context[k] for k in ("run_id", "question", "focus_pair")} if context else None
    return {"plan_id": pid, "plan": plan, "usage": PLANS[pid]["usage"], "model": llm.model, "provider": llm.provider,
            "follow_up": follow}


def propose_figure_plan(files: list[dict], dataset: str, note: str = "", llm: LLM | None = None,
                        provider: str | None = None) -> dict:
    """Figure mode: uploads -> extracted spec -> plan, stored like a question's plan."""
    from app.agent.figure import plan_from_figure
    llm = llm or new_llm(provider)
    out = plan_from_figure(llm, files, dataset, note)
    pid = uuid.uuid4().hex[:12]
    usage = [u.as_dict() for u in out["usages"]]
    with _lock:
        PLANS[pid] = {"question": out["question"], "dataset": out["plan"]["dataset"], "plan": out["plan"],
                      "usage": usage, "llm": llm, "figure": out["figure"]}
    return {"plan_id": pid, "plan": out["plan"], "usage": usage, "model": llm.model, "provider": llm.provider,
            "figure": out["figure"], "question": out["question"]}


def start_run(plan_id: str, edits: dict, step_through: bool = False, confirmed_fields: list[str] | None = None,
              plant_overclaim: bool = False) -> str:
    """Approve the stored plan with the user's edits (validated in code) and start it in the background.

    A figure plan runs only once the user has confirmed every low-confidence extracted setting (SPEC §4:
    consequential choices need human approval, enforced here rather than in the prompt).
    """
    from app.agent.figure import low_confidence
    with _lock:
        stored = PLANS.get(plan_id)
    if stored is None:
        raise KeyError(f"unknown plan {plan_id}")
    if stored.get("figure"):
        missing = [n for n in low_confidence(stored["figure"]["spec"]) if n not in (confirmed_fields or [])]
        if missing:
            raise PlanInvalid([f"Confirm these low-confidence settings read from the figure first: {', '.join(missing)}"], None)
    plan = {**stored["plan"], **{k: v for k, v in edits.items() if k in EDITABLE}}
    if "steps" in edits:  # the user may drop steps, never add tools the planner did not propose
        proposed = {s["tool"] for s in stored["plan"]["steps"]}
        plan["steps"] = [s for s in edits["steps"] if s.get("tool") in proposed]
    approved = to_approved(plan)  # raises PlanInvalid with the problems
    run = new_run(stored["question"], approved)
    run.plan_usage = stored["usage"]
    run.figure = stored.get("figure")
    run.proposal = stored["plan"]
    handle = RunHandle(run, step_through=step_through)
    with _lock:
        RUNS[run.run_id] = handle
    remember(stored.get("session_id"), last_run_id=run.run_id)
    llm: LLM = stored["llm"]

    plant = plant_overclaim and verifier.planting_allowed()

    def work():
        run_investigation(llm, run, on_event=handle.emit, wait_for_next=handle.wait_for_next)
        if run.status == "awaiting_verification":
            verify_in_background(run, handle.emit, plant)
        if ON_RUN_FINISHED:
            ON_RUN_FINISHED()

    threading.Thread(target=work, name=f"run-{run.run_id}", daemon=True).start()
    return run.run_id


def verify_in_background(run: Run, emit, plant: bool = False) -> None:
    """The report is already shown ("Verifying..."); this one Muse Wonder call then sets verified / flagged."""
    if plant:
        verifier.plant_overclaim(run)
    run.verification = {"status": "pending"}
    run.save()
    emit({"type": "verification_started", "planted": run.planted})
    v = verifier.verify_run(run)
    run.save()
    emit({"type": "verification_finished", "status": run.status, "verification": v})


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
