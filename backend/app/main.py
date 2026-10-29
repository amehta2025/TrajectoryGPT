"""FastAPI app: thin HTTP wrappers around the science functions. No science here."""
import os

import numpy as np
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import PlainTextResponse, Response

from app.config import dataset_config, load_config
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
    allow_methods=["GET"],
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
    return {"status": "ok", "datasets": sorted(load_config()["datasets"])}


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
