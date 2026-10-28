"""Residue-residue minimum heavy-atom distances and contact frequencies.

Contact definition (fixed): two residues are in contact in a frame if their
minimum heavy-atom distance <= cutoff (Angstrom).
"""
import hashlib
import json
import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Callable

import MDAnalysis as mda
import numpy as np
from MDAnalysis.lib.distances import distance_array

from app.config import CACHE_DIR, dataset_config
from app.science.loader import HEAVY, dataset_files, group_heavy_atoms, load_universe
from app.science.residues import residue_keys

log = logging.getLogger(__name__)

CUTOFF_MIN, CUTOFF_MAX = 2.0, 10.0
CACHE_VERSION = 1  # bump when the precompute algorithm changes
CONTACT_DEFINITION = "min heavy-atom distance <= cutoff (Å)"


@dataclass
class MinDistances:
    """min_dist[frame, i, j] in Å for residue keys_a[i], keys_b[j], over all frames."""
    min_dist: np.ndarray
    keys_a: list[str]
    keys_b: list[str]
    pbc: str
    cache_path: Path


@lru_cache(maxsize=8)
def _file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def dataset_hashes(dataset: str) -> dict[str, str]:
    top, traj = dataset_files(dataset)
    return {Path(top).name: _file_sha256(top), Path(traj).name: _file_sha256(traj)}


def _cache_key(dataset: str, group_a: str, group_b: str, ag_a, ag_b) -> str:
    payload = {
        "v": CACHE_VERSION,
        "dataset": dataset,
        "files": dataset_hashes(dataset),
        "groups": [group_a, group_b],
        "selection": HEAVY,
        "atoms_a": ag_a.indices.tolist(),
        "atoms_b": ag_b.indices.tolist(),
        "mdanalysis": mda.__version__,
        "numpy": np.__version__,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


def _residue_starts(ag) -> np.ndarray:
    """Index of the first atom of each residue (atoms are contiguous per residue)."""
    resindices = ag.resindices
    return np.flatnonzero(np.r_[True, resindices[1:] != resindices[:-1]])


def precompute_min_distances(
    dataset: str,
    group_a: str,
    group_b: str,
    *,
    cache_dir: Path = CACHE_DIR,
    progress: Callable[[str], None] | None = None,
) -> MinDistances:
    """Compute (or load from cache) min heavy-atom distance per residue pair per frame."""
    ag_a = group_heavy_atoms(dataset, group_a)
    ag_b = group_heavy_atoms(dataset, group_b)
    if np.intersect1d(ag_a.indices, ag_b.indices).size:
        raise ValueError(f"Groups {group_a} and {group_b} overlap; choose disjoint groups.")
    keys_a = residue_keys(dataset, ag_a.residues)
    keys_b = residue_keys(dataset, ag_b.residues)

    u = load_universe(dataset)
    has_box = u.dimensions is not None and np.all(u.dimensions[:3] > 0)
    pbc = "minimum-image" if has_box else "none"

    path = Path(cache_dir) / f"mindist_{dataset}_{group_a}_{group_b}_{_cache_key(dataset, group_a, group_b, ag_a, ag_b)}.npy"
    if path.exists():
        return MinDistances(np.load(path), keys_a, keys_b, pbc, path)

    n_frames = u.trajectory.n_frames
    msg = f"Precomputing {group_a}-{group_b} distances on {dataset}: {n_frames} frames (first time only)"
    log.info(msg)
    if progress:
        progress(msg)

    starts_a, starts_b = _residue_starts(ag_a), _residue_starts(ag_b)
    out = np.empty((n_frames, len(starts_a), len(starts_b)), dtype=np.float32)
    buf = np.empty((len(ag_a), len(ag_b)), dtype=np.float64)
    for ts in u.trajectory:
        box = ts.dimensions if has_box else None
        distance_array(ag_a.positions, ag_b.positions, box=box, result=buf)
        per_res_a = np.minimum.reduceat(buf, starts_a, axis=0)
        out[ts.frame] = np.minimum.reduceat(per_res_a, starts_b, axis=1)

    path.parent.mkdir(parents=True, exist_ok=True)
    np.save(path, out)
    return MinDistances(out, keys_a, keys_b, pbc, path)


def validate_window(n_frames: int, start: int, stop: int | None, stride: int) -> range:
    stop = n_frames if stop is None else stop
    if stride < 1:
        raise ValueError("stride must be >= 1")
    if not (0 <= start < stop <= n_frames):
        raise ValueError(f"Frame window [{start}, {stop}) is outside 0..{n_frames}")
    return range(start, stop, stride)


def validate_cutoff(cutoff: float) -> float:
    if not (CUTOFF_MIN <= cutoff <= CUTOFF_MAX):
        raise ValueError(f"cutoff must be between {CUTOFF_MIN} and {CUTOFF_MAX} Å, got {cutoff}")
    return float(cutoff)


def calculate_contacts(
    dataset: str,
    group_a: str,
    group_b: str,
    cutoff: float = 4.5,
    start: int = 0,
    stop: int | None = None,
    stride: int = 1,
    *,
    cache_dir: Path = CACHE_DIR,
    progress: Callable[[str], None] | None = None,
) -> dict:
    """Contact frequency matrix freq[i, j] = fraction of window frames with min_dist <= cutoff."""
    cutoff = validate_cutoff(cutoff)
    md = precompute_min_distances(dataset, group_a, group_b, cache_dir=cache_dir, progress=progress)
    frames = validate_window(md.min_dist.shape[0], start, stop, stride)
    window = md.min_dist[frames.start:frames.stop:frames.step]
    freq = (window <= cutoff).mean(axis=0)
    cfg = dataset_config(dataset)
    return {
        "dataset": dataset,
        "group_a": group_a,
        "group_b": group_b,
        "keys_a": md.keys_a,
        "keys_b": md.keys_b,
        "frequency": freq,
        "frames": np.asarray(frames),
        "n_frames": len(frames),
        "cutoff": cutoff,
        "contact_definition": CONTACT_DEFINITION,
        "pbc": md.pbc,
        "units": {"distance": "Å", "time": cfg["time_label"], "dt_ns": cfg["dt_ns"]},
    }
