import numpy as np
import pytest

from app.science.loader import load_universe
from app.science.structure import (
    MAX_FRAMES_PER_REQUEST, frames_float32, playback_stride, structure_pdb, viewer_atoms,
)

DS = "adk_transition"


def _pdb_atoms(pdb: str) -> list[tuple[str, int, str, np.ndarray]]:
    out = []
    for line in pdb.splitlines():
        if line.startswith(("ATOM", "HETATM")):
            xyz = np.array([float(line[30:38]), float(line[38:46]), float(line[46:54])])
            out.append((line[12:16].strip(), int(line[22:26]), line[17:21].strip(), xyz))
    return out


def test_pdb_and_frames_have_same_atom_order():
    ag = viewer_atoms(DS)
    atoms = _pdb_atoms(structure_pdb(DS, frame=5))
    assert len(atoms) == len(ag)
    assert [(n, r) for n, r, _, _ in atoms] == list(zip(ag.names, ag.resids.tolist()))
    _, coords = frames_float32(DS, 5, 6, 1)
    pdb_xyz = np.stack([x for *_, x in atoms])
    np.testing.assert_allclose(pdb_xyz, coords[0], atol=1e-3)  # PDB has 3 decimals


def test_viewer_atoms_are_heavy_only():
    names = viewer_atoms(DS).names
    assert not any(n.startswith("H") for n in names)


def test_frames_match_trajectory():
    idx, coords = frames_float32(DS, 10, 40, 10)
    assert idx.tolist() == [10, 20, 30]
    assert coords.dtype == np.float32
    u = load_universe(DS)
    ag = viewer_atoms(DS)
    u.trajectory[20]
    np.testing.assert_allclose(coords[1], ag.positions, atol=1e-5)


def test_frame_cap_and_window(monkeypatch):
    # adk_transition has only 98 frames, so lower the cap to exercise it.
    monkeypatch.setattr("app.science.structure.MAX_FRAMES_PER_REQUEST", 50)
    with pytest.raises(ValueError, match="At most 50"):
        frames_float32(DS, 0, None, 1)
    frames_float32(DS, 0, None, 2)  # 49 frames: allowed
    with pytest.raises(ValueError):
        frames_float32(DS, 0, 999, 1)
    assert MAX_FRAMES_PER_REQUEST == 300


def test_playback_stride():
    assert playback_stride(4187) == 28
    assert playback_stride(98) == 1
