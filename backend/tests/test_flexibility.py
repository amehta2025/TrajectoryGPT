"""RMSF (SPEC §10b e): the Kabsch fit on synthetic data with a known answer, and a cross-check against
MDAnalysis's own AverageStructure + AlignTraj + RMSF on adk_transition."""
import warnings

import MDAnalysis as mda
import numpy as np
import pytest
from MDAnalysis.analysis import align, rms

from app.science.flexibility import residue_flexibility, rmsf_after_fit, superpose
from app.science.loader import dataset_files, group_selection
from app.science.residues import parse_residue_key

DS = "adk_transition"


def _rotation(rng) -> np.ndarray:
    q, r = np.linalg.qr(rng.normal(size=(3, 3)))
    q *= np.sign(np.diag(r))
    if np.linalg.det(q) < 0:
        q[:, 0] *= -1
    return q


def _rigid_copies(ref: np.ndarray, n: int, rng) -> np.ndarray:
    return np.stack([ref @ _rotation(rng).T + rng.normal(scale=5, size=3) for _ in range(n)])


def test_superpose_undoes_rigid_motion():
    rng = np.random.default_rng(0)
    ref = rng.normal(scale=10, size=(30, 3))
    moved = _rigid_copies(ref, 12, rng)
    out = superpose(moved, ref, np.ones(30, bool))
    assert np.allclose(out, ref, atol=1e-8)


def test_rmsf_is_zero_for_rigid_motion_and_recovers_known_noise():
    rng = np.random.default_rng(1)
    ref = rng.normal(scale=10, size=(40, 3))
    sigma = 0.5
    frames = []
    for _ in range(4000):
        x = ref.copy()
        x[-1] += rng.normal(scale=sigma, size=3)  # one atom jiggles; the rest are rigid
        frames.append(x @ _rotation(rng).T + rng.normal(scale=3, size=3))
    fit = np.ones(40, bool)
    fit[-1] = False  # fit on the rigid atoms only, like fitting on CORE
    r = rmsf_after_fit(np.stack(frames), fit)
    assert np.all(r[:-1] < 1e-6)
    assert r[-1] == pytest.approx(sigma * np.sqrt(3), rel=0.05)


def test_rmsf_ignores_a_global_rotation():
    rng = np.random.default_rng(2)
    coords = rng.normal(scale=10, size=(50, 25, 3)) * 0.05 + rng.normal(scale=10, size=(25, 3))
    rot = _rotation(rng)
    fit = np.ones(25, bool)
    assert np.allclose(rmsf_after_fit(coords, fit), rmsf_after_fit(coords @ rot.T, fit), atol=1e-8)


def test_residue_flexibility_matches_mdanalysis_on_transition():
    r = residue_flexibility(DS)
    assert len(r["keys"]) == 214 and r["rmsf"].shape == (214,)
    assert parse_residue_key(r["keys"][0]).resid == 1
    assert "Path RMSF" in r["note"]

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        u = mda.Universe(*dataset_files(DS), in_memory=True)  # own copy: alignment rewrites coordinates
        sel = f"name CA and ({group_selection(DS, 'CORE')})"
        avg = align.AverageStructure(u, u, select=sel, ref_frame=0).run()
        align.AlignTraj(u, avg.results.universe, select=sel, in_memory=True).run()
        ref = rms.RMSF(u.select_atoms("protein and name CA")).run().results.rmsf
    assert np.allclose(r["rmsf"], ref, atol=0.05)
    # LID (122-159) moves most along the closed-to-open path; CORE moves least.
    lid = r["rmsf"][(r["resids"] >= 122) & (r["resids"] <= 159)].mean()
    core = r["rmsf"][(r["resids"] >= 160)].mean()
    assert lid > 2 * core


def test_residue_flexibility_rejects_a_single_frame():
    with pytest.raises(ValueError):
        residue_flexibility(DS, 0, 1)
