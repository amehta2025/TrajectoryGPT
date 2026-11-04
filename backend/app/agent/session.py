"""Server-side state for the UI: proposed plans, running investigations, and their event streams.

Plans are stored here when proposed; a run starts only from a stored plan plus the user's edits, which
are validated again in code (SPEC §9 step 4). Each run executes in a background thread and appends
events to a list that the SSE endpoint streams (late subscribers replay from the start).
"""
from __future__ import annotations

import json
import os
import re
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from app.agent.llm import LLM, CostTracker
from app.config import CACHE_DIR
from app.agent import orchestrator, scope, verifier
from app.agent.orchestrator import Run, new_run, run_investigation
from app.agent.planner import PlanInvalid, make_plan, to_approved
from app.agent.tools import res_label
from app.safety import budget

RUN_BUDGET_USD = float(os.environ.get("RUN_BUDGET_USD", "0.50"))
EDITABLE = ("cutoff", "robustness_cutoffs", "window_a", "window_b", "stride", "sequence_exclusion", "group_a", "group_b",
            "native_selection", "native_radius", "ref_frames")


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


PLANS: dict[str, dict] = {}  # this container's copy; every plan is also saved under PLANS_DIR
# On Modal PLANS_DIR is on the Volume, so a plan survives a scale-down, a redeploy or a second container
# (the live bug: "unknown plan" when Run was clicked after the container that made the plan had gone).
PLANS_DIR = CACHE_DIR / "plans"
PLAN_ID = re.compile(r"[0-9a-f]{12}")
ON_PLAN_SAVED = None  # set on Modal to commit the Volume, so another container sees the plan at once
RELOAD_STORE = None  # set on Modal to reload the Volume before reading a plan this container hasn't seen


class PlanExpired(KeyError):
    """The plan is in neither this container's memory nor the store; the UI re-plans."""


class FileClaims:
    """One plan starts one run, recorded as <plan_id>.claim beside the plan. Creating the file is atomic
    (O_EXCL) on one filesystem; Modal swaps in a claim store shared by every container (modal_app.py)."""

    def _path(self, plan_id: str) -> Path:
        return PLANS_DIR / f"{plan_id}.claim"

    def get(self, plan_id: str) -> str | None:
        try:
            return self._path(plan_id).read_text(encoding="utf-8").strip() or None
        except FileNotFoundError:
            return None

    def claim(self, plan_id: str, run_id: str) -> str:
        """Claim the plan for run_id; returns the run that holds the claim (run_id, or an earlier one)."""
        PLANS_DIR.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(self._path(plan_id), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return self.get(plan_id) or run_id
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(run_id)
        return run_id


CLAIMS = FileClaims()
_PLAN_LOCKS: dict[str, threading.Lock] = {}


def _save_plan(pid: str, entry: dict) -> None:
    """Write the plan (without its model client) to the store; the provider name is enough to rebuild it."""
    PLANS_DIR.mkdir(parents=True, exist_ok=True)
    d = {k: v for k, v in entry.items() if k not in ("llm", "run_id")} | {"provider": entry["llm"].provider}
    tmp = PLANS_DIR / f"{pid}.json.tmp"
    tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, PLANS_DIR / f"{pid}.json")
    if ON_PLAN_SAVED:
        try:
            ON_PLAN_SAVED()
        except Exception:  # the plan is on disk; Modal also commits in the background
            pass


def get_plan(plan_id: str) -> dict:
    """A stored plan: from memory, else from the store (reloading it once, for a plan made elsewhere)."""
    with _lock:
        stored = PLANS.get(plan_id)
    if stored is not None:
        return stored
    if not PLAN_ID.fullmatch(plan_id or ""):
        raise PlanExpired(f"unknown plan {plan_id}")
    path = PLANS_DIR / f"{plan_id}.json"
    if not path.exists() and RELOAD_STORE:
        try:
            RELOAD_STORE()
        except Exception:  # Modal refuses a reload while files are open; the plan is then simply missing
            pass
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        raise PlanExpired(f"unknown plan {plan_id}") from None
    d["llm"] = new_llm(d.pop("provider", None))
    with _lock:
        return PLANS.setdefault(plan_id, d)


def _plan_lock(plan_id: str) -> threading.Lock:
    with _lock:
        return _PLAN_LOCKS.setdefault(plan_id, threading.Lock())


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
                 selected_pair: str | None = None, follow_up: bool = False, provider: str | None = None,
                 skip_scope: bool = False) -> dict:
    """A follow-up uses the session's last run (its params, findings, and the pair "that" refers to).
    skip_scope: the user clicked "Ask anyway" on a declined question, so plan it without the scope gate."""
    from app.agent.limits import residue_check
    llm = llm or new_llm(provider)
    if selected_pair is not None:
        remember(session_id, selected_pair=selected_pair)
    context = follow_up_context(session_id) if follow_up else None
    dataset = context["plan"]["dataset"] if context else dataset  # a follow-up stays on the previous dataset
    check = residue_check(question, dataset)
    if check:  # a residue that doesn't match the protein: the user picks one before anything is planned
        return {"declined": check, "usage": [], "model": llm.model, "provider": llm.provider}
    gate, gate_usages = (None, []) if skip_scope else scope.check(llm, question, has_context=context is not None)
    if gate:  # nonsense, off-topic, other protein, creative, or "that" with nothing before it: no plan, no tools
        return {"declined": gate, "usage": [u.as_dict() for u in gate_usages], "model": llm.model,
                "provider": llm.provider}
    plan, usages = make_plan(llm, question, dataset, context)
    usages = gate_usages + usages
    pid = uuid.uuid4().hex[:12]
    entry = {"question": question, "dataset": dataset, "plan": plan, "usage": [u.as_dict() for u in usages],
             "llm": llm, "session_id": session_id}
    _save_plan(pid, entry)
    with _lock:
        PLANS[pid] = entry
    follow = {k: context[k] for k in ("run_id", "question", "focus_pair")} if context else None
    return {"plan_id": pid, "plan": plan, "usage": PLANS[pid]["usage"], "model": llm.model, "provider": llm.provider,
            "follow_up": follow}


def propose_figure_plan(files: list[dict], dataset: str, note: str = "", llm: LLM | None = None,
                        provider: str | None = None) -> dict:
    """Figure mode: uploads -> extracted spec -> plan, stored like a question's plan."""
    from app.agent.figure import plan_from_figure
    llm = llm or new_llm(provider)
    return _store_figure_plan(llm, plan_from_figure(llm, files, dataset, note), [])


READS: dict[str, dict] = {}  # figures read but not planned yet (POST /figure/read, then /figure/plan with read_id)


def read_figure(files: list[dict], note: str = "", llm: LLM | None = None, provider: str | None = None) -> dict:
    """Figure mode, first half: what the model read from the figure, shown while the plan is drafted."""
    from app.agent.figure import read_figure as read
    llm = llm or new_llm(provider)
    figure, usages = read(llm, files, note)
    rid = uuid.uuid4().hex[:12]
    usage = [u.as_dict() for u in usages]
    with _lock:
        READS[rid] = {"figure": figure, "usage": usage, "llm": llm}
        while len(READS) > MAX_SESSIONS:
            READS.pop(next(iter(READS)))
    return {"read_id": rid, "figure": figure, "usage": usage, "model": llm.model, "provider": llm.provider}


def plan_figure_read(read_id: str, dataset: str) -> dict:
    """Figure mode, second half: the plan for a figure read by read_figure(), stored like any plan."""
    from app.agent.figure import plan_from_spec
    with _lock:
        read = READS.pop(read_id, None)
    if read is None:
        raise KeyError(f"unknown figure read {read_id}")
    llm = read["llm"]
    return _store_figure_plan(llm, plan_from_spec(llm, read["figure"], dataset), read["usage"])


def _store_figure_plan(llm: LLM, out: dict, read_usage: list[dict]) -> dict:
    pid = uuid.uuid4().hex[:12]
    usage = read_usage + [u.as_dict() for u in out["usages"]]
    entry = {"question": out["question"], "dataset": out["plan"]["dataset"], "plan": out["plan"],
             "usage": usage, "llm": llm, "figure": out["figure"]}
    _save_plan(pid, entry)
    with _lock:
        PLANS[pid] = entry
    return {"plan_id": pid, "plan": out["plan"], "usage": usage, "model": llm.model, "provider": llm.provider,
            "figure": out["figure"], "question": out["question"]}


def start_run(plan_id: str, edits: dict, step_through: bool = False, confirmed_fields: list[str] | None = None,
              plant_overclaim: bool = False) -> str:
    """Approve the stored plan with the user's edits (validated in code) and start it in the background.

    A figure plan runs only once the user has confirmed every low-confidence extracted setting (SPEC §4:
    consequential choices need human approval, enforced here rather than in the prompt).

    One plan starts one run: a repeated or concurrent call (a retry, a double click, a script) gets the run
    the first call started, with its edits, and is never billed twice (red-team: double run from one plan).
    The claim lives in CLAIMS, so this holds across a restart and across containers too.
    """
    from app.agent.figure import low_confidence
    stored = get_plan(plan_id)  # PlanExpired if it is nowhere
    with _plan_lock(plan_id):
        if stored.get("run_id"):
            return stored["run_id"]
        if claimed := CLAIMS.get(plan_id):  # started earlier, by this container before a restart or by another one
            stored["run_id"] = claimed
            return claimed
        if stored.get("figure"):
            missing = [n for n in low_confidence(stored["figure"]["spec"]) if n not in (confirmed_fields or [])]
            if missing:
                raise PlanInvalid([f"Confirm these low-confidence settings read from the figure first: {', '.join(missing)}"], None)
        plan = {**stored["plan"], **{k: v for k, v in edits.items() if k in EDITABLE}}
        if "steps" in edits:  # the user may drop steps, never add tools the planner did not propose
            proposed = {s["tool"] for s in stored["plan"]["steps"]}
            plan["steps"] = [s for s in edits["steps"] if s.get("tool") in proposed]
        approved = to_approved(plan)  # raises PlanInvalid with the problems; the plan stays unclaimed
        run = new_run(stored["question"], approved)
        run.plan_usage = stored["usage"]
        run.figure = stored.get("figure")
        run.proposal = stored["plan"]
        winner = CLAIMS.claim(plan_id, run.run_id)
        stored["run_id"] = winner
        if winner != run.run_id:  # another container started this plan a moment ago
            return winner
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
    run.verification = verifier.pending()
    run.save()  # a restart now leaves 'pending' on disk, which run_view shows as timed out
    emit({"type": "verification_started", "planted": run.planted})
    worker = threading.Thread(target=verifier.verify_run, args=(run,), name=f"verify-{run.run_id}", daemon=True)
    worker.start()
    worker.join(verifier.TIMEOUT_S)
    if worker.is_alive():
        verifier.settle(run, verifier.timed_out())
    run.save()
    emit({"type": "verification_finished", "status": run.status, "verification": run.verification})


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
    d["verification"] = verifier.resolve_stale(d.get("verification"), live=h is not None)
    return d
