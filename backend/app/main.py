"""FastAPI app: thin HTTP wrappers around the science functions. No science here."""
import threading

import numpy as np
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import PlainTextResponse, Response

from app.config import dataset_config, load_config
from app.science.contacts import CONTACT_DEFINITION, calculate_contacts
from app.science.distances import distance_over_time
from app.science.loader import group_selection, load_universe, pbc_mode
from app.science.structure import frames_float32, playback_stride, structure_pdb, viewer_atoms

app = FastAPI(title="TrajectoryGPT")
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET"],
    expose_headers=["X-Frames", "X-N-Atoms"],
)

# Universes are shared and reading a frame moves the trajectory cursor, so one request at a time.
_traj_lock = threading.Lock()


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
