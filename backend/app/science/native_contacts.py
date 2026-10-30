"""Fraction of native contacts vs frame, against chosen reference frames (SPEC §5c).

Wraps MDAnalysis.analysis.contacts.Contacts with the same setup as contacts.q1q2:
for each reference frame, the native contacts are the atom pairs of `selection` within
`radius` in that frame, and Q(frame) uses the 'radius_cut' method. The defaults
(CA atoms, 8 Å, frames 0 and last) reproduce the MDAnalysis User Guide Q1 vs Q2 example.
"""
import warnings

import MDAnalysis as mda
import numpy as np
from MDAnalysis.analysis import contacts
from MDAnalysis.analysis.contacts import Contacts

from app.config import dataset_config
from app.science.contacts import validate_window
from app.science.loader import dataset_files, load_universe, pbc_mode

RADIUS_MIN, RADIUS_MAX = 2.0, 15.0


def _reference_group(dataset: str, selection: str, frame: int):
    """The selection at one frame, in its own Universe so the reference stays fixed."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        u = mda.Universe(*dataset_files(dataset))
    u.trajectory[frame]
    return u.select_atoms(selection)


def native_contacts(
    dataset: str,
    selection: str = "name CA",
    radius: float = 8.0,
    ref_frames: tuple[int, ...] = (0, -1),
    start: int = 0,
    stop: int | None = None,
    stride: int = 1,
) -> dict:
    """q[frame, k] = fraction of reference frame k's native contacts present in each frame."""
    if not (RADIUS_MIN <= radius <= RADIUS_MAX):
        raise ValueError(f"radius must be between {RADIUS_MIN} and {RADIUS_MAX} Å, got {radius}")
    u = load_universe(dataset)
    n_frames = u.trajectory.n_frames
    if not ref_frames:
        raise ValueError("Give at least one reference frame")
    refs = []
    for f in ref_frames:
        if not -n_frames <= f < n_frames:
            raise ValueError(f"Reference frame {f} is outside 0..{n_frames - 1}")
        refs.append(f % n_frames)
    frames = validate_window(n_frames, start, stop, stride)
    try:
        if len(u.select_atoms(selection)) == 0:
            raise ValueError(f"Selection {selection!r} matches no atoms")
    except mda.SelectionError as e:
        raise ValueError(f"Invalid selection {selection!r}: {e}") from None

    groups = (selection, selection)
    refgroups = [(_reference_group(dataset, selection, f),) * 2 for f in refs]
    ca = Contacts(
        u, groups, refgroups, radius=radius, method="radius_cut",
        pbc=pbc_mode(dataset) == "minimum-image",
    ).run(start=frames.start, stop=frames.stop, step=frames.step)
    ts = ca.results.timeseries  # columns: frame, q_ref0, q_ref1, ...

    cfg = dataset_config(dataset)
    frames_arr = ts[:, 0].astype(int)
    return {
        "dataset": dataset,
        "selection": selection,
        "radius": float(radius),
        "ref_frames": refs,
        "n_native": [int(np.count_nonzero(m)) for m in ca.initial_contacts],
        "frames": frames_arr,
        "time_ns": None if cfg["dt_ns"] is None else frames_arr * cfg["dt_ns"],
        "q": ts[:, 1:],
        "method": "MDAnalysis.analysis.contacts.Contacts, method='radius_cut' (as contacts.q1q2)",
        "pbc": pbc_mode(dataset),
        "units": {"distance": "Å", "time": cfg["time_label"], "dt_ns": cfg["dt_ns"]},
    }


REPRO_TOLERANCE = 1e-6  # max |ours - published code| for an exact reproduction


def reproduction_check(dataset: str, selection: str = "name CA", radius: float = 8.0) -> dict:
    """Exact-reproduction check (SPEC §5c): our native_contacts vs the published method itself.

    The reference is the MDAnalysis User Guide's code, contacts.q1q2(u, selection, radius), run in a fresh
    Universe. Ours is native_contacts() with the same parameters and reference frames 0 and last.
    """
    if not (RADIUS_MIN <= radius <= RADIUS_MAX):
        raise ValueError(f"radius must be between {RADIUS_MIN} and {RADIUS_MAX} Å, got {radius}")
    ours = native_contacts(dataset, selection, radius, (0, -1))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        u = mda.Universe(*dataset_files(dataset))
    ref = contacts.q1q2(u, selection, radius=radius).run().results.timeseries  # columns: frame, Q1, Q2
    if ref.shape != (len(ours["frames"]), 3) or not np.array_equal(ref[:, 0].astype(int), ours["frames"]):
        raise ValueError("reference and our frames differ; cannot compare")
    diff = np.abs(ours["q"] - ref[:, 1:])
    max_diff = float(diff.max())
    return {
        "dataset": dataset, "selection": selection, "radius": float(radius), "ref_frames": ours["ref_frames"],
        "frames": ours["frames"], "time_ns": ours["time_ns"], "ours": ours["q"], "reference": ref[:, 1:],
        "max_abs_diff": max_diff, "n_values": int(diff.size), "tolerance": REPRO_TOLERANCE,
        "match": max_diff <= REPRO_TOLERANCE,
        "reference_method": f"MDAnalysis.analysis.contacts.q1q2(u, {selection!r}, radius={radius}) (MDAnalysis User Guide)",
        "endpoints": {"q1_first": float(ours["q"][0, 0]), "q1_last": float(ours["q"][-1, 0]),
                      "q2_first": float(ours["q"][0, 1]), "q2_last": float(ours["q"][-1, 1])},
        "units": ours["units"],
    }
