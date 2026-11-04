"""Replay mode (SPEC §12): curated real runs play back without calling any LLM.

Active when APP_MODE=replay (the kill switch) or when today's AI budget is used up. A question (or the
sample figure) that matches a recorded run gets that run's plan; approving it plays the recorded turns
back through the same live step log, then the recorded report and verifier result. Every replayed run is
labelled "Recorded run". The science endpoints, charts and 3D viewer stay live.

Recorded runs live in backend/replay/<run_id>.json (committed; stripped of encrypted reasoning), figures
they used in backend/replay/figures/. scripts/curate_replay.py copies a saved run there.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import threading
import time
import uuid
from functools import lru_cache
from pathlib import Path

from app.config import REPO_ROOT

REPLAY_DIR = Path(os.environ.get("REPLAY_DIR") or REPO_ROOT / "backend" / "replay")
MATCH_RATIO = 0.85
PACE = 0.5        # replay at half the recorded model + tool time ...
MAX_STEP_S = 3.0  # ... and at most this long per turn


def mode() -> tuple[str, str | None]:
    """("live" | "replay", why). Replay when switched on, or when today's budget is used up."""
    if os.environ.get("APP_MODE", "live") == "replay":
        return "replay", "switched to recorded runs"
    from app.safety import budget
    if budget.spent_today() >= budget.daily_limit_usd():
        return "replay", "today's AI budget is used up"
    return "live", None


@lru_cache(maxsize=1)
def _load_all(directory: str) -> dict[str, dict]:
    out = {}
    for p in sorted(Path(directory).glob("*.json")):
        try:
            r = json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            continue
        out[r["run_id"]] = r
    return out


def recorded(directory: Path | None = None) -> dict[str, dict]:
    return _load_all(str(directory or REPLAY_DIR))


def catalog(directory: Path | None = None) -> list[dict]:
    """What the start screen offers in replay mode."""
    return [{"run_id": r["run_id"], "label": r.get("replay_label") or r["question"], "question": r["question"],
             "dataset": r["plan"]["dataset"], "figure": bool(r.get("figure")),
             "figure_id": (r.get("figure") or {}).get("figure_id")} for r in recorded(directory).values()]


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower().replace("–", "-")).strip()


def match_question(question: str, directory: Path | None = None) -> dict | None:
    """The recorded run whose question or label matches (case, punctuation and small typos ignored)."""
    q = _norm(question)
    best, best_ratio = None, 0.0
    for r in recorded(directory).values():
        if r.get("figure"):
            continue
        for cand in (r["question"], r.get("replay_label") or ""):
            ratio = difflib.SequenceMatcher(None, q, _norm(cand)).ratio() if cand else 0.0
            if ratio > best_ratio:
                best, best_ratio = r, ratio
    return best if best_ratio >= MATCH_RATIO else None


def match_figure(raw_files: list[tuple[str, str, bytes]], directory: Path | None = None) -> dict | None:
    """The recorded figure run made from the same image (by SHA-256)."""
    digests = {hashlib.sha256(raw).hexdigest() for _, _, raw in raw_files}
    for r in recorded(directory).values():
        if (r.get("figure") or {}).get("sha256") in digests:
            return r
    return None


def proposal(r: dict) -> dict:
    """The recorded plan as /plan returns it; approving plan_id 'replay:<run_id>' plays the run back."""
    plan = r.get("proposal") or {**r["plan"], "summary": "Recorded plan."}
    return {"plan_id": f"replay:{r['run_id']}", "plan": plan, "usage": [], "model": "recorded run",
            "figure": r.get("figure"), "question": r["question"], "recorded": True, "follow_up": None}


def figure_path(figure_id: str, directory: Path | None = None) -> tuple[bytes, str] | None:
    d = (directory or REPLAY_DIR) / "figures"
    if not figure_id.isalnum() or not (d / figure_id).exists():
        return None
    meta = json.loads((d / f"{figure_id}.json").read_text(encoding="utf-8"))
    return (d / figure_id).read_bytes(), meta["mime"]


def start_playback(plan_id: str, step_through: bool = False, directory: Path | None = None) -> str:
    """Play a recorded run back as a new in-memory run (id replay-<hex>); returns the new run id."""
    from app.agent import orchestrator, session
    from app.agent.tools import ApprovedPlan, RunState

    rid = plan_id.removeprefix("replay:")
    r = recorded(directory).get(rid)
    if r is None:
        raise KeyError(f"unknown recorded run {rid}")
    plan = ApprovedPlan(**r["plan"])
    run = orchestrator.Run(f"replay-{uuid.uuid4().hex[:10]}", r["question"], plan, RunState(plan))
    run.figure, run.plan_usage, run.recorded_from, run.proposal = r.get("figure"), r.get("plan_usage", []), rid, r.get("proposal")
    handle = session.RunHandle(run, step_through=step_through)
    with session._lock:
        session.RUNS[run.run_id] = handle
    threading.Thread(target=_play, args=(r, run, handle), name=f"replay-{rid}", daemon=True).start()
    return run.run_id


def _play(r: dict, run, handle) -> None:
    from app.agent.orchestrator import _auto_check, _event
    state, emit = run.state, handle.emit
    findings = list(r["findings"].values())
    emit({"type": "run_started", "run_id": run.run_id, "checklist": state.checklist, "recorded": True})
    for rec in r["turns"]:
        emit({"type": "turn_started", "turn": rec["turn"]})
        time.sleep(min(MAX_STEP_S, PACE * ((rec.get("model_s") or 0) + (rec.get("tool_s") or 0))))
        pid = rec.get("provenance_id")
        if pid and pid in r["results"]:
            state.results[pid] = r["results"][pid]
        if rec["tool"] == "record_finding" and rec["status"] == "ok" and findings:
            f = findings.pop(0)
            state.findings[f["id"]] = f
        if rec["tool"] == "submit_report" and rec["status"] == "submitted":
            state.report = r["report"]
        _auto_check(state, rec["tool"], rec["status"])
        run.turns.append(rec)
        ev = {"type": "turn", **_event(rec, state)}
        if pid:
            ev["result"] = {"status": rec["status"], "tool": rec["tool"], "provenance_id": pid}
        emit(ev)
        handle.wait_for_next()
    state.checklist, state.extra_steps, run.problems = r["checklist"], r.get("extra_steps", []), r.get("problems", [])
    v = r.get("verification")
    run.status = "awaiting_verification" if v and v.get("status") in ("verified", "flagged") else r["status"]
    run.wall_s = r.get("wall_s", 0.0)
    emit({"type": "run_finished", "status": run.status, "report": state.report, "problems": run.problems,
          "cost_usd": run.cost_usd(), "wall_s": run.wall_s, "error": r.get("error"), "recorded": True})
    if run.status == "awaiting_verification":
        run.verification = {"status": "pending"}
        emit({"type": "verification_started", "planted": r.get("planted")})
        time.sleep(min(MAX_STEP_S, PACE * (v.get("wall_s") or 2)))
        run.verification, run.status, run.planted = v, v["status"], r.get("planted")
        emit({"type": "verification_finished", "status": run.status, "verification": v})


def curate(run: dict, label: str | None = None, directory: Path | None = None, figures_dir: Path | None = None) -> Path:
    """Copy a saved run into the replay set: drop encrypted reasoning and the model's raw input history."""
    d = directory or REPLAY_DIR
    d.mkdir(parents=True, exist_ok=True)
    out = {k: v for k, v in run.items() if k != "input_items"}
    if label:
        out["replay_label"] = label
    for t in out["turns"]:  # the timeline's copies hold reasoning summaries only, never encrypted content
        for k in ("saw", "output"):
            t[k] = [{kk: vv for kk, vv in it.items() if kk != "encrypted_content"} for it in t.get(k, [])]
    fig = out.get("figure")
    if fig and figures_dir is not None:
        src = figures_dir / fig["figure_id"]
        (d / "figures").mkdir(exist_ok=True)
        raw = src.read_bytes()
        (d / "figures" / fig["figure_id"]).write_bytes(raw)
        (d / "figures" / f"{fig['figure_id']}.json").write_text(
            json.dumps({"filename": fig["filename"], "mime": fig["mime"]}), encoding="utf-8")
        fig.setdefault("sha256", hashlib.sha256(raw).hexdigest())  # the upload's digest (a PDF's, if the figure was extracted)
    path = d / f"{out['run_id']}.json"
    path.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    _load_all.cache_clear()
    return path
