"""FastAPI app: thin HTTP wrappers around the science functions. No science here."""
import base64
import json
import os
import re

import httpx
import numpy as np
from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import PlainTextResponse, Response, StreamingResponse

from app.agent import session, verifier
from app.agent.llm import PROVIDERS, available_providers
from app.agent.planner import PlanInvalid
from app.config import dataset_config, load_config
from app.safety import budget, replay
from app.safety.ratelimit import LIMITER, RateLimited, client_ip
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
            "model": os.environ.get("META_MODEL", "muse-spark-1.3"), "mode": replay.mode()[0],
            "mode_reason": replay.mode()[1],
            "spent_today_usd": round(budget.spent_today(), 4), "daily_budget_usd": budget.daily_limit_usd(),
            "verifier_model": os.environ.get("META_VERIFIER_MODEL", "muse-wonder-1.0"),
            "dev_overclaim_toggle": verifier.planting_allowed(), "sandbox": os.environ.get("SANDBOX_ENABLED") == "1",
            "providers": available_providers(), "default_provider": os.environ.get("MODEL_PROVIDER", "meta"),
            "open_model": os.environ.get("BASETEN_MODEL", "meta-models/muse-spark-open-1026"),
            "voice": "meta" in available_providers() and replay.mode()[0] == "live",
            # The start screen asks for the passcode before planning, and offers recorded runs without it.
            "passcode_required": replay.mode()[0] == "live" and not passcode_ok(None)}


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

def _ip(request: Request) -> str:
    return client_ip(request.client.host if request.client else None, request.headers.get("x-forwarded-for"))


def require_passcode(request: Request, x_demo_passcode: str | None = Header(None)):
    """Live AI work needs the passcode; replay mode calls no model, so recorded runs are open to everyone.
    Wrong passcodes are limited per IP (10 an hour, on every endpoint), so guessing is slow (red-team F2)."""
    if replay.mode()[0] == "replay":
        return
    ip = _ip(request)
    wait = LIMITER.blocked("passcode_fail", ip)
    if wait:
        raise HTTPException(429, f"Too many wrong passcodes. Try again in about {max(1, round(wait / 60))} min.",
                            headers={"Retry-After": str(wait)})
    if not passcode_ok(x_demo_passcode):
        if x_demo_passcode:  # an empty header is "not entered yet", not a guess
            try:
                LIMITER.check("passcode_fail", ip)
            except RateLimited:
                pass
        raise HTTPException(401, "Demo passcode required for AI investigations.")


def rate_limit(kind: str):
    """Per-IP hourly limit (app/safety/ratelimit.py), keyed on the real client IP."""
    def dep(request: Request):
        ip = _ip(request)
        try:
            LIMITER.check(kind, ip)
        except RateLimited as e:
            raise HTTPException(429, str(e), headers={"Retry-After": str(e.retry_after_s)}) from None
    return dep


def _replay_miss() -> HTTPException:
    return HTTPException(409, {"replay": True, "recorded": replay.catalog(),
                               "message": "The AI is off right now, so only recorded investigations can be shown. "
                                          "Pick one of them; charts and the 3D viewer stay live."})


def _check_provider(provider: str | None) -> None:
    """None = the default (MODEL_PROVIDER). A provider without its key set is refused before any call."""
    if provider is None:
        return
    if provider not in PROVIDERS:
        raise HTTPException(400, f"provider must be one of {sorted(PROVIDERS)}")
    if provider not in available_providers():
        raise HTTPException(503, "That model is not available on this server right now.")


def require_budget():
    try:
        budget.check()
    except budget.BudgetReached as e:
        raise HTTPException(503, str(e)) from None


@app.post("/auth/check", dependencies=[Depends(require_passcode)])
def auth_check():
    return {"ok": True}


@app.get("/replays")
def replays():
    """Recorded runs available in replay mode (also shown on the start screen while it is on)."""
    return {"mode": replay.mode()[0], "recorded": replay.catalog()}


@app.post("/plan", dependencies=[Depends(require_passcode), Depends(rate_limit("plan"))])
def plan(question: str = Body(...), dataset: str = Body("adk_equilibrium"), session_id: str | None = Body(None),
         selected_pair: str | None = Body(None), follow_up: bool = Body(False), provider: str | None = Body(None)):
    _check_dataset(dataset)
    if not question.strip() or len(question) > 2000:
        raise HTTPException(400, "question must be 1-2000 characters")
    if session_id is not None and not SESSION_ID.fullmatch(session_id):
        raise HTTPException(400, "bad session_id")
    if selected_pair is not None and not PAIR_LABEL.fullmatch(selected_pair):
        raise HTTPException(400, "selected_pair must look like 'HSE134-ASP118'")
    if replay.mode()[0] == "replay":
        r = replay.match_question(question)
        if r is None:
            raise _replay_miss()
        return replay.proposal(r)
    _check_provider(provider)
    require_budget()
    try:
        return session.propose_plan(question.strip(), dataset, session_id=session_id, selected_pair=selected_pair,
                                    follow_up=follow_up, provider=provider)
    except PlanInvalid as e:
        raise HTTPException(422, {"errors": e.errors, "plan": e.plan}) from None


SESSION_ID = re.compile(r"[A-Za-z0-9-]{8,64}")
PAIR_LABEL = re.compile(r"[A-Z]{3}\d{1,4}-[A-Z]{3}\d{1,4}")


@app.post("/figure/plan", dependencies=[Depends(require_passcode), Depends(rate_limit("figure"))])
def figure_plan(files: list[dict] = Body(...), dataset: str = Body("adk_equilibrium"), note: str = Body(""),
                provider: str | None = Body(None)):
    """Figure mode: 1-2 files (PNG/JPEG figure, PDF methods), base64, at most 10 MB each."""
    from app.agent.figure import NOTE_LIMIT, UnsupportedFigure, UploadRejected, decode_upload
    _check_dataset(dataset)
    if replay.mode()[0] == "replay":
        try:
            r = replay.match_figure([decode_upload(f) for f in files[:2]])
        except UploadRejected as e:
            raise HTTPException(400, str(e)) from None
        if r is None:
            raise _replay_miss()
        return replay.proposal(r)
    _check_provider(provider)
    require_budget()
    try:
        return session.propose_figure_plan(files, dataset, note[:NOTE_LIMIT], provider=provider)
    except UploadRejected as e:
        raise HTTPException(400, str(e)) from None
    except UnsupportedFigure as e:
        raise HTTPException(422, {"unsupported": str(e), "reason": e.reason, "spec": e.spec, "figure": e.figure}) from None
    except PlanInvalid as e:
        raise HTTPException(422, {"errors": e.errors, "plan": e.plan}) from None


@app.post("/transcribe", dependencies=[Depends(require_passcode), Depends(rate_limit("voice"))])
def transcribe(audio: str = Body(..., embed=True)):
    """Push-to-talk (SPEC §9d): base64 WAV (mono, 16-bit, 16/24 kHz) -> text for the question box."""
    from app.agent import voice
    if replay.mode()[0] == "replay":
        raise HTTPException(409, "Voice input is off while the AI is off.")
    require_budget()
    if len(audio) > voice.MAX_BYTES * 4 // 3 + 4:
        raise HTTPException(413, f"Recording too long: keep it under {voice.MAX_S:.0f} seconds.")
    try:
        out = voice.transcribe(base64.b64decode(audio, validate=True))
    except (ValueError, voice.AudioRejected) as e:  # binascii.Error is a ValueError
        raise HTTPException(400, str(e) if isinstance(e, voice.AudioRejected) else "audio must be base64") from None
    except httpx.HTTPStatusError as e:
        raise HTTPException(502, f"Transcription failed ({e.response.status_code}). Type the question instead.") from None
    budget.record({"provider": "meta", "model": out["model"], "effort": "n/a", "audio_s": out["seconds"],
                   "latency_s": out["latency_s"], "cost_usd": out["cost_usd"]})
    return {"text": out["text"], "seconds": out["seconds"]}


@app.get("/figures/{figure_id}")
def get_figure(figure_id: str):
    from app.agent.figure import load_figure
    try:
        raw, mime = load_figure(figure_id)
    except KeyError:
        recorded = replay.figure_path(figure_id)  # figures of recorded runs ship with the replay set
        if recorded is None:
            raise HTTPException(404, "unknown figure") from None
        raw, mime = recorded
    return Response(content=raw, media_type=mime)


@app.post("/replays/{run_id}/plan", dependencies=[Depends(rate_limit("replay"))])
def replay_plan(run_id: str):
    """A recorded run's plan, in any mode and without the passcode: no model is called ("Watch a recorded run")."""
    r = replay.recorded().get(run_id)
    if r is None:
        raise HTTPException(404, "unknown recorded run")
    return replay.proposal(r)


@app.post("/runs", dependencies=[Depends(rate_limit("run"))])
def start_run(request: Request, plan_id: str = Body(...), edits: dict = Body({}), step_through: bool = Body(False),
              confirmed_fields: list[str] = Body([]), plant_overclaim: bool = Body(False),
              x_demo_passcode: str | None = Header(None)):
    if plan_id.startswith("replay:"):  # a recorded run: no model is called and no passcode needed; edits do not apply
        try:
            return {"run_id": replay.start_playback(plan_id, step_through)}
        except KeyError as e:
            raise HTTPException(404, str(e)) from None
    require_passcode(request, x_demo_passcode)
    require_budget()
    try:
        return {"run_id": session.start_run(plan_id, edits, step_through, confirmed_fields, plant_overclaim)}
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
    """Server-sent events: one `data:` line per step-log event; ends after run_finished, or after
    verification_finished when a report was accepted (the verifier runs in the background).
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


@app.get("/runs/{run_id}/export", response_class=PlainTextResponse)
def export_run(run_id: str):
    """reproduce_<run_id>.py: a standalone script that recomputes every stored value (SPEC §11)."""
    from app.provenance import export
    run = get_run(run_id)
    try:
        script = export.build_script(run)
    except ValueError as e:
        raise HTTPException(409, str(e)) from None
    return PlainTextResponse(script, media_type="text/x-python",
                             headers={"Content-Disposition": f'attachment; filename="{export.script_name(run_id)}"'})


def _sandbox_file(run_id: str):
    from app.agent.orchestrator import INVESTIGATIONS_DIR
    return INVESTIGATIONS_DIR / "sandbox" / f"{run_id}.json"


def _sandbox_done(run_id: str, job: dict) -> None:
    path = _sandbox_file(run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(job), encoding="utf-8")
    if session.ON_RUN_FINISHED:
        session.ON_RUN_FINISHED()  # on Modal: commit the Volume


@app.post("/runs/{run_id}/sandbox", dependencies=[Depends(require_passcode), Depends(rate_limit("sandbox"))])
def sandbox_start(run_id: str):
    """Re-run the export in a clean Modal Sandbox (deployed app only: SANDBOX_ENABLED=1)."""
    from app.provenance import export, sandbox
    if os.environ.get("SANDBOX_ENABLED") != "1":
        raise HTTPException(503, "The clean-sandbox check runs on the deployed app only; run the downloaded script locally instead.")
    script = export.build_script(get_run(run_id))
    return sandbox.start(run_id, script, on_done=_sandbox_done)


@app.get("/runs/{run_id}/sandbox")
def sandbox_status(run_id: str):
    from app.provenance import sandbox
    get_run(run_id)  # 404 for an unknown run
    if run_id in sandbox.JOBS:
        return sandbox.JOBS[run_id]
    path = _sandbox_file(run_id)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"status": "none"}


@app.post("/runs/{run_id}/next")
def run_next(run_id: str, request: Request, step_through: bool | None = Body(None, embed=True),
             x_demo_passcode: str | None = Header(None)):
    if not run_id.startswith("replay-"):  # stepping through a recorded playback needs no passcode
        require_passcode(request, x_demo_passcode)
    _handle(run_id)
    session.next_turn(run_id, step_through)
    return {"ok": True}
