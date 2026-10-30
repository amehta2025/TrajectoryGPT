"""Per-residue flexibility: RMSF of C-alpha atoms over a frame window (SPEC §10b e).

Every frame is superposed on the CORE C-alpha atoms (least squares, Kabsch), first onto the window's
first frame and then onto the average structure, and RMSF is the root-mean-square deviation of each
C-alpha from its average position. Superposing on CORE means LID and NMP motion relative to CORE
shows up as flexibility. adk_equilibrium is already fit to CORE, so the fit there changes almost
nothing; on adk_transition the frames are a biased closed-to-open path, so this is a path RMSF, not
equilibrium flexibility.
"""
import numpy as np

from app.config import dataset_config
from app.science.contacts import validate_window
from app.science.loader import group_selection, load_universe
from app.science.residues import residue_keys

CA = "protein and name CA"
FIT_GROUP = "CORE"
FIT_PASSES = 2  # onto the first frame, then onto the average structure


def superpose(coords: np.ndarray, ref: np.ndarray, fit: np.ndarray) -> np.ndarray:
    """Rigidly superpose every frame of coords (n_frames, n_atoms, 3) on ref (n_atoms, 3), fitting only
    the atoms where fit is True. Least squares with reflection correction (Kabsch)."""
    p = coords[:, fit]
    q = ref[fit]
    cp = p.mean(axis=1, keepdims=True)
    cq = q.mean(axis=0)
    h = np.einsum("nai,aj->nij", p - cp, q - cq)
    u, _, vt = np.linalg.svd(h)
    d = np.sign(np.linalg.det(np.einsum("nij,njk->nik", vt.transpose(0, 2, 1), u.transpose(0, 2, 1))))
    diag = np.zeros_like(h)
    diag[:, 0, 0] = 1
    diag[:, 1, 1] = 1
    diag[:, 2, 2] = d
    rot = np.einsum("nij,njk,nkl->nil", vt.transpose(0, 2, 1), diag, u.transpose(0, 2, 1))
    return np.einsum("nij,naj->nai", rot, coords - cp) + cq


def rmsf_after_fit(coords: np.ndarray, fit: np.ndarray, passes: int = FIT_PASSES) -> np.ndarray:
    """RMSF per atom after superposing every frame on the fit atoms (first frame, then the average)."""
    x = coords.astype(np.float64)
    ref = x[0]
    for _ in range(passes):
        x = superpose(x, ref, fit)
        ref = x.mean(axis=0)
    return np.sqrt(((x - x.mean(axis=0)) ** 2).sum(axis=-1).mean(axis=0))


def residue_flexibility(dataset: str, start: int = 0, stop: int | None = None, stride: int = 1) -> dict:
    """C-alpha RMSF per residue over [start, stop) with the given stride, fit on CORE C-alphas."""
    u = load_universe(dataset)
    ca = u.select_atoms(CA)
    fit = np.isin(ca.indices, u.select_atoms(f"name CA and ({group_selection(dataset, FIT_GROUP)})").indices)
    frames = validate_window(u.trajectory.n_frames, start, stop, stride)
    if len(frames) < 2:
        raise ValueError("RMSF needs at least two frames")
    coords = np.stack([ca.positions.copy() for _ in u.trajectory[frames.start:frames.stop:frames.step]])
    rmsf = rmsf_after_fit(coords, fit)
    cfg = dataset_config(dataset)
    note = ("Fit on CORE C-alphas, so LID and NMP motion relative to CORE shows up as flexibility."
            if cfg["stats_valid"] else
            "Path RMSF, not equilibrium flexibility: the frames are a biased closed-to-open path (fit on CORE C-alphas).")
    return {
        "dataset": dataset,
        "keys": residue_keys(dataset, ca.residues),
        "resids": ca.resids.astype(int),
        "rmsf": rmsf,
        "fit_group": FIT_GROUP,
        "fit_passes": FIT_PASSES,
        "frames": np.asarray(frames),
        "n_frames": len(frames),
        "note": note,
        "units": {"distance": "Å", "time": cfg["time_label"], "dt_ns": cfg["dt_ns"]},
    }
