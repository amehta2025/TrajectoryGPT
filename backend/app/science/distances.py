"""Minimum heavy-atom distance per frame for one residue pair."""
import numpy as np
from MDAnalysis.lib.distances import distance_array

from app.config import dataset_config
from app.science.contacts import validate_window
from app.science.loader import HEAVY, load_universe
from app.science.residues import parse_residue_key


def _residue_heavy_atoms(u, dataset: str, key: str):
    k = parse_residue_key(key)
    if k.dataset != dataset:
        raise ValueError(f"Residue key {key!r} is not from dataset {dataset!r}")
    ag = u.select_atoms(f"({HEAVY}) and segid {k.segid} and resid {k.resid}")
    if len(ag) == 0 or ag.residues[0].resname != k.resname:
        raise ValueError(f"Residue {key!r} not found")
    return ag


def distance_over_time(
    dataset: str, key_a: str, key_b: str, start: int = 0, stop: int | None = None, stride: int = 1
) -> dict:
    u = load_universe(dataset)
    ag_a = _residue_heavy_atoms(u, dataset, key_a)
    ag_b = _residue_heavy_atoms(u, dataset, key_b)
    if key_a == key_b:
        raise ValueError("Choose two different residues")
    frames = validate_window(u.trajectory.n_frames, start, stop, stride)

    dist = np.empty(len(frames), dtype=np.float64)
    has_box = u.dimensions is not None and np.all(u.dimensions[:3] > 0)
    for n, ts in enumerate(u.trajectory[frames.start:frames.stop:frames.step]):
        box = ts.dimensions if has_box else None
        dist[n] = distance_array(ag_a.positions, ag_b.positions, box=box).min()

    cfg = dataset_config(dataset)
    frames_arr = np.asarray(frames)
    return {
        "dataset": dataset,
        "key_a": key_a,
        "key_b": key_b,
        "frames": frames_arr,
        "time_ns": None if cfg["dt_ns"] is None else frames_arr * cfg["dt_ns"],
        "distance": dist,
        "pbc": "minimum-image" if has_box else "none",
        "units": {"distance": "Å", "time": cfg["time_label"], "dt_ns": cfg["dt_ns"]},
    }
