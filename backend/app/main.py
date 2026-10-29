"""FastAPI app: thin HTTP wrappers around the science functions. No science here."""
import json
import os

import numpy as np
from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import PlainTextResponse, Response, StreamingResponse

from app.agent import session
from app.agent.planner import PlanInvalid
from app.config import dataset_config, load_config
from app.safety import budget
from app.safety.passcode import HEADER as PASSCODE_HEADER, passcode_ok
from app.science.contact_stats import StatsRefused, compare_intervals, contact_statistics
from app.science.contacts import CONTACT_DEFINITION, calculate_contacts
from app.science.distances import distance_over_time
from app.science.loader import TRAJ_LOCK, group_selection, load_universe, pbc_mode
from app.science.native_contacts import native_contacts
from app.science.structure import frames_float32, playback_stride, structure_pdb, viewer_atoms

# Deployed: only the frontend domain(s) in ALLOWED_ORIGINS (comma-separated). Unset: local dev server.
ALLOWED_ORIGINS = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()] or [
    "http://localhost:5173", "http://127.0.0.1:5173",
]

app = FastAPI(title="TrajectoryGPT")
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", PASSCODE_HEADER],
    expose_headers=["X-Frames", "X-N-Atoms"],
)

# One trajectory read at a time (shared with the agent's tools).
_traj_lock = TRAJ_LOCK


def _check_dataset(dataset: str) -> dict:
    try:
        return dataset_config(dataset)
    except ValueError as e:
        raise HTTPException(404, str(e)) from None


def _call(fn, *args, **kwargs):
    with _traj_lock:
        try:
            return fn(*args, **kwargs)
        except ValueError as e:
            raise HTTPException(400, str(e)) from None


def _stats_call(fn, *args, **kwargs):
    """Like _call, but a refusal (stats on a stats_valid: false dataset) is a normal result for the UI."""
    try:
        return {"status": "ok", **_call(fn, *args, **kwargs)}
    except StatsRefused as e:
        return {"status": "refused", "reason": str(e)}


def _round(a: np.ndarray, nd: int = 4) -> list:
    return np.round(a.astype(np.float64), nd).tolist()


@app.get("/health")
def health():
    return {"status": "ok", "datasets": sorted(load_config()["datasets"]),
            "model": os.environ.get("META_MODEL", "muse-spark-1.3"), "mode": os.environ.get("APP_MODE", "live"),
            "spent_today_usd": round(budget.spent_today(), 4), "daily_budget_usd": budget.daily_limit_usd()}


@app.get("/datasets")
def datasets():
    return [
        {"id": k, "description": v["description"].strip(), "stats_valid": v["stats_valid"], "time_label": v["time_label"]}
        for k, v in load_config()["datasets"].items()
    ]


@app.get("/metadata/{dataset}")
def metadata(dataset: str):
    cfg = _check_dataset(dataset)

    def _meta():
        u = load_universe(dataset)
        n_frames = u.trajectory.n_frames
        return {
            "dataset": dataset,
            "n_atoms": u.atoms.n_atoms,
            "n_viewer_atoms": len(viewer_atoms(dataset)),
            "n_residues": u.residues.n_residues,
            "segids": sorted(set(u.segments.segids)),
            "n_frames": n_frames,
            "dt_ns": cfg["dt_ns"],
            "time_label": cfg["time_label"],
            "has_box": u.trajectory.ts.dimensions is not None,
            "pbc": pbc_mode(dataset),
            "stats_valid": cfg["stats_valid"],
            "playback_stride": playback_stride(n_frames),
            "groups": {g: {"ranges": r, "selection": group_selection(dataset, g)} for g, r in cfg["domains"].items()},
            "groups_source": cfg["domains_source"]["citation"].strip(),
            "sequence_exclusion_default": load_config()["sequence_exclusion"],
            "contact_definition": CONTACT_DEFINITION,
            "license": cfg["license"],
            "citation": cfg["citation"].strip(),
        }

    return _call(_meta)


@app.get("/contacts/{dataset}")
def contacts(
    dataset: str,
    group_a: str,
    group_b: str,
    cutoff: float = 4.5,
    start: int = 0,
    stop: int | None = None,
    stride: int = 1,
    sequence_exclusion: int | None = None,
):
    _check_dataset(dataset)
    r = _call(calculate_contacts, dataset, group_a, group_b, cutoff, start, stop, stride, sequence_exclusion)
    return {
        **{k: r[k] for k in ("dataset", "group_a", "group_b", "keys_a", "keys_b", "sequence_exclusion",
                             "n_frames", "cutoff", "contact_definition", "pbc", "units")},
        "frequency": _round(r["frequency"]),
        "excluded": r["excluded"].tolist(),
        "window": {"start": start, "stop": stop, "stride": stride},
    }


@app.get("/distances/{dataset}")
def distances(dataset: str, key_a: str, key_b: str, start: int = 0, stop: int | None = None, stride: int = 1):
    _check_dataset(dataset)
    r = _call(distance_over_time, dataset, key_a, key_b, start, stop, stride)
    return {
        **{k: r[k] for k in ("dataset", "key_a", "key_b", "pbc", "units")},
        "frames": r["frames"].tolist(),
        "time_ns": None if r["time_ns"] is None else _round(r["time_ns"]),
        "distance": _round(r["distance"]),
    }


@app.get("/structure/{dataset}", response_class=PlainTextResponse)
def structure(dataset: str, frame: int = 0):
    _check_dataset(dataset)
    return PlainTextResponse(_call(structure_pdb, dataset, frame), media_type="chemical/x-pdb")


@app.get("/frames/{dataset}")
def frames(dataset: str, start: int = 0, stop: int | None = None, stride: int = Query(1, ge=1)):
    """Little-endian float32 [n_frames, n_atoms, 3]; frame indices in the X-Frames header."""
    _check_dataset(dataset)
    idx, coords = _call(frames_float32, dataset, start, stop, stride)
    return Response(
        content=coords.astype("<f4").tobytes(),
        media_type="application/octet-stream",
        headers={"X-Frames": ",".join(map(str, idx.tolist())), "X-N-Atoms": str(coords.shape[1])},
    )


@app.get("/compare/{dataset}")
def compare(
    dataset: str,
    group_a: str,
    group_b: str,
    a_start: int,
    a_stop: int,
    b_start: int,
    b_stop: int,
    cutoff: float = 4.5,
    stride: int = 1,
    sequence_exclusion: int | None = None,
):
    _check_dataset(dataset)
    return _stats_call(compare_intervals, dataset, group_a, group_b, (a_start, a_stop), (b_start, b_stop),
                       cutoff, stride, sequence_exclusion)


@app.get("/contact_stats/{dataset}")
def contact_stats(dataset: str, key_a: str, key_b: str, cutoff: float = 4.5,
                  start: int = 0, stop: int | None = None, stride: int = 1):
    _check_dataset(dataset)
    r = _stats_call(contact_statistics, dataset, key_a, key_b, cutoff, start, stop, stride)
    if r["status"] == "ok":
        r["frames"] = r["frames"].tolist()
        r["in_contact"] = r["in_contact"].astype(int).tolist()
    return r


@app.get("/native_contacts/{dataset}")
def native_contacts_endpoint(dataset: str, selection: str = "name CA", radius: float = 8.0,
                             ref_frames: list[int] = Query([0, -1]),
                             start: int = 0, stop: int | None = None, stride: int = 1):
    _check_dataset(dataset)
    r = _call(native_contacts, dataset, selection, radius, tuple(ref_frames), start, stop, stride)
    return {
        **{k: r[k] for k in ("dataset", "selection", "radius", "ref_frames", "n_native", "method", "pbc", "units")},
        "frames": r["frames"].tolist(),
        "time_ns": None if r["time_ns"] is None else _round(r["time_ns"]),
        "q": _round(r["q"].T, 6),  # one list per reference frame
    }


# ---------- agent (SPEC §9): plan -> approve -> run with a live step log ----------
# Starting AI work needs the demo passcode and budget left today (SPEC §12). Reading a run (GET, by its
# random id) and the science endpoints above stay public.

def require_passcode(x_demo_passcode: str | None = Header(None)):
    if not passcode_ok(x_demo_passcode):
        raise HTTPException(401, "Demo passcode required for AI investigations.")


def require_budget():
    try:
        budget.check()
    except budget.BudgetReached as e:
        raise HTTPException(503, str(e)) from None


@app.post("/auth/check", dependencies=[Depends(require_passcode)])
def auth_check():
    return {"ok": True}


@app.post("/plan", dependencies=[Depends(require_passcode), Depends(require_budget)])
def plan(question: str = Body(...), dataset: str = Body("adk_equilibrium")):
    _check_dataset(dataset)
    if not question.strip() or len(question) > 2000:
        raise HTTPException(400, "question must be 1-2000 characters")
    try:
        return session.propose_plan(question.strip(), dataset)
    except PlanInvalid as e:
        raise HTTPException(422, {"errors": e.errors, "plan": e.plan}) from None


@app.post("/runs", dependencies=[Depends(require_passcode), Depends(require_budget)])
def start_run(plan_id: str = Body(...), edits: dict = Body({}), step_through: bool = Body(False)):
    try:
        return {"run_id": session.start_run(plan_id, edits, step_through)}
    except KeyError as e:
        raise HTTPException(404, str(e)) from None
    except PlanInvalid as e:
        raise HTTPException(422, {"errors": e.errors}) from None


def _handle(run_id: str):
    try:
        return session.get_handle(run_id)
    except KeyError as e:
        raise HTTPException(404, str(e)) from None


@app.get("/runs", dependencies=[Depends(require_passcode)])
def list_runs(limit: int = Query(50, ge=1, le=500)):
    from app.agent.orchestrator import list_runs as _list
    return _list(limit)


@app.get("/runs/{run_id}")
def get_run(run_id: str):
    if "/" in run_id or ".." in run_id:
        raise HTTPException(404, "unknown run")
    try:
        return session.run_view(run_id)
    except KeyError as e:
        raise HTTPException(404, str(e)) from None


@app.get("/runs/{run_id}/events")
def run_events(run_id: str, after: int = 0):
    """Server-sent events: one `data:` line per step-log event; ends after run_finished.
    A run that is no longer in memory (saved on disk) streams one run_finished event."""
    try:
        h = session.get_handle(run_id)
    except KeyError:
        v = get_run(run_id)
        done = {"seq": 0, "type": "run_finished", **{k: v[k] for k in ("status", "problems", "cost_usd", "wall_s", "error")}}
        return StreamingResponse(iter([f"data: {json.dumps(done)}\n\n"]), media_type="text/event-stream")

    def gen():
        for e in h.stream(after):
            yield ": keep-alive\n\n" if e is None else f"data: {json.dumps(e)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/runs/{run_id}/next", dependencies=[Depends(require_passcode)])
def run_next(run_id: str, step_through: bool | None = Body(None, embed=True)):
    _handle(run_id)
    session.next_turn(run_id, step_through)
    return {"ok": True}
