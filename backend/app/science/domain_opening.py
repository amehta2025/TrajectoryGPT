"""Domain opening over time (SPEC §10b c): one coordinate per frame, the distance between the centre of
mass of a mobile domain (LID or NMP) and that of CORE, in Å.

Centres of mass are mass-weighted over all protein atoms of each domain (ranges from config/dataset.yaml).
A distance between two centres is unchanged by the rigid CORE fit applied to adk_equilibrium, so the fit
does not affect it. On adk_transition the frames are a biased closed-to-open path in path steps, not time.
The standard AdK domain angles are not used: no citable definition is recorded yet (SPEC §10b c).
"""
import numpy as np

from app.config import dataset_config
from app.science.contacts import validate_window
from app.science.loader import group_selection, load_universe

REFERENCE = "CORE"


def domain_opening(dataset: str, domain: str, start: int = 0, stop: int | None = None, stride: int = 1) -> dict:
    """COM distance between `domain` and CORE for each frame in [start, stop) with the given stride."""
    cfg = dataset_config(dataset)
    if domain == REFERENCE or domain not in cfg["domains"]:
        mobile = sorted(d for d in cfg["domains"] if d != REFERENCE)
        raise ValueError(f"domain must be one of {mobile}, got {domain!r}")
    u = load_universe(dataset)
    mob = u.select_atoms(f"protein and ({group_selection(dataset, domain)})")
    ref = u.select_atoms(f"protein and ({group_selection(dataset, REFERENCE)})")
    frames = validate_window(u.trajectory.n_frames, start, stop, stride)
    idx = np.arange(frames.start, frames.stop, frames.step)
    dist = np.empty(len(idx))
    for i, _ in enumerate(u.trajectory[frames.start:frames.stop:frames.step]):
        dist[i] = np.linalg.norm(mob.center_of_mass() - ref.center_of_mass())
    note = ("Centre-of-mass distance; unaffected by the fit to CORE."
            if cfg["stats_valid"] else
            "Centre-of-mass distance along a biased closed-to-open path (path steps, not time).")
    return {
        "dataset": dataset,
        "domain": domain,
        "reference": REFERENCE,
        "frames": idx,
        "time_ns": None if cfg["dt_ns"] is None else idx * cfg["dt_ns"],
        "distance": dist,
        "n_frames": len(idx),
        "note": note,
        "units": {"distance": "Å", "time": cfg["time_label"], "dt_ns": cfg["dt_ns"]},
    }
