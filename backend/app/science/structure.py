"""Reference structure (PDB) and packed coordinates for the 3D viewer (SPEC §10a).

Both come from the same heavy-atom AtomGroup, so atom i in the PDB is atom i in
every coordinate frame.
"""
import io
import math
import warnings

import numpy as np
from MDAnalysis.coordinates.PDB import PDBWriter
from MDAnalysis.lib.util import NamedStream

from app.science.contacts import validate_window
from app.science.loader import HEAVY, load_universe

MAX_FRAMES_PER_REQUEST = 300
PLAYBACK_TARGET_FRAMES = 150


def viewer_atoms(dataset: str):
    """The one AtomGroup used for both the PDB and the coordinate frames."""
    return load_universe(dataset).select_atoms(HEAVY)


def playback_stride(n_frames: int) -> int:
    """Stride giving about PLAYBACK_TARGET_FRAMES frames (4187 -> 28, 98 -> 1)."""
    return max(1, math.ceil(n_frames / PLAYBACK_TARGET_FRAMES))


def structure_pdb(dataset: str, frame: int = 0) -> str:
    """PDB text of the viewer atoms at `frame`."""
    ag = viewer_atoms(dataset)
    u = ag.universe
    validate_window(u.trajectory.n_frames, frame, frame + 1, 1)
    u.trajectory[frame]
    buf = io.StringIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # missing chainIDs/occupancies in PSF topologies
        with PDBWriter(NamedStream(buf, "structure.pdb"), n_atoms=len(ag), multiframe=False) as w:
            w.write(ag)
    return buf.getvalue()


def frames_float32(dataset: str, start: int, stop: int | None, stride: int) -> tuple[np.ndarray, np.ndarray]:
    """(frame indices, float32 coordinates [n_frames, n_atoms, 3] in Å)."""
    ag = viewer_atoms(dataset)
    traj = ag.universe.trajectory
    frames = validate_window(traj.n_frames, start, stop, stride)
    if len(frames) > MAX_FRAMES_PER_REQUEST:
        raise ValueError(f"At most {MAX_FRAMES_PER_REQUEST} frames per request, asked for {len(frames)}; raise the stride")
    out = np.empty((len(frames), len(ag), 3), dtype=np.float32)
    for n, _ in enumerate(traj[frames.start:frames.stop:frames.step]):
        out[n] = ag.positions
    return np.asarray(frames), out
