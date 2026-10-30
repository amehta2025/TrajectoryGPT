"""Check the precompute against a plain-numpy hand calculation on three pairs.

Pairs (LID x NMP on adk_transition), chosen to cover three cases:
- 158 ASP - 36 ARG: clearly in contact in the closed state (about 2.6 Å).
- 157 LYS - 36 ARG: crosses the 4.5 Å cutoff (4.5001 Å at frame 14, 4.4965 Å at frame 36).
- 136 LYS - 44 GLU: far apart in every frame (>= 35 Å).
"""
import numpy as np
import pytest

from app.science.contacts import calculate_contacts, precompute_min_distances
from app.science.distances import distance_over_time
from app.science.loader import load_universe
from app.science.residues import make_residue_key

DS = "adk_transition"
CONTACT, NEAR, FAR = (158, 36), (157, 36), (136, 44)


def hand_min_distance(lid_resid: int, nmp_resid: int) -> np.ndarray:
    """All heavy-atom pairs, Euclidean distance, minimum. No MDAnalysis distance code."""
    u = load_universe(DS)
    a = u.select_atoms(f"resid {lid_resid} and not name H*")
    b = u.select_atoms(f"resid {nmp_resid} and not name H*")
    out = []
    for _ in u.trajectory:
        diff = a.positions[:, None, :].astype(np.float64) - b.positions[None, :, :]
        out.append(np.sqrt((diff**2).sum(-1)).min())
    return np.array(out)


@pytest.fixture(scope="module")
def mindist(tmp_path_factory):
    return precompute_min_distances(DS, "LID", "NMP", cache_dir=tmp_path_factory.mktemp("cache"))


def _index(md, lid_resid, nmp_resid):
    keys_a = [int(k.split(":")[2]) for k in md.keys_a]
    keys_b = [int(k.split(":")[2]) for k in md.keys_b]
    return keys_a.index(lid_resid), keys_b.index(nmp_resid)


@pytest.mark.parametrize("pair", [CONTACT, NEAR, FAR], ids=["contact", "near_cutoff", "far"])
def test_precompute_matches_hand_calculation(mindist, pair):
    i, j = _index(mindist, *pair)
    np.testing.assert_allclose(mindist.min_dist[:, i, j], hand_min_distance(*pair), atol=1e-3)


@pytest.mark.parametrize("pair", [CONTACT, NEAR, FAR], ids=["contact", "near_cutoff", "far"])
def test_frequency_matches_hand_calculation(mindist, pair):
    res = calculate_contacts(DS, "LID", "NMP", cutoff=4.5, cache_dir=mindist.cache_path.parent)
    i, j = _index(mindist, *pair)
    assert res["frequency"][i, j] == pytest.approx((hand_min_distance(*pair) <= 4.5).mean())


def test_three_cases_behave_as_labelled(mindist):
    d = mindist.min_dist
    assert d[:, *_index(mindist, *CONTACT)].min() < 3.0
    near = d[:, *_index(mindist, *NEAR)]
    assert near[14] > 4.5 and near[36] <= 4.5  # either side of the cutoff
    assert d[:, *_index(mindist, *FAR)].min() > 30.0


def test_cache_is_reused(mindist):
    again = precompute_min_distances(DS, "LID", "NMP", cache_dir=mindist.cache_path.parent)
    assert mindist.computed and not again.computed
    assert again.cache_path == mindist.cache_path
    np.testing.assert_array_equal(again.min_dist, mindist.min_dist)


def test_window_and_stride(mindist):
    res = calculate_contacts(DS, "LID", "NMP", start=10, stop=50, stride=5, cache_dir=mindist.cache_path.parent)
    assert res["frames"].tolist() == list(range(10, 50, 5))
    assert res["frequency"].shape == (38, 30)
    assert res["units"]["time"] == "path step" and res["units"]["dt_ns"] is None


def test_sequence_exclusion_default_and_override(mindist):
    cache = mindist.cache_path.parent
    res = calculate_contacts(DS, "NMP", "CORE", cache_dir=cache)
    assert res["sequence_exclusion"] == 2  # dataset.yaml default
    ra = [int(k.split(":")[2]) for k in res["keys_a"]]
    rb = [int(k.split(":")[2]) for k in res["keys_b"]]
    i, j = ra.index(30), rb.index(28)
    assert res["excluded"][i, j] and not res["excluded"][i, rb.index(27)]
    assert res["excluded"].sum() == 6  # 28-30, 29-30, 29-31, 59-60, 59-61, 58-60
    assert not calculate_contacts(DS, "NMP", "CORE", sequence_exclusion=0, cache_dir=cache)["excluded"].any()


@pytest.mark.parametrize("kwargs", [{"sequence_exclusion": -1}, {"sequence_exclusion": 11}, {"sequence_exclusion": 1.5}, {"cutoff": 1.0}, {"cutoff": 12.0}, {"start": 50, "stop": 10}, {"stop": 999}, {"stride": 0}])
def test_rejects_bad_params(mindist, kwargs):
    with pytest.raises(ValueError):
        calculate_contacts(DS, "LID", "NMP", cache_dir=mindist.cache_path.parent, **kwargs)


def test_rejects_overlapping_groups(tmp_path):
    with pytest.raises(ValueError):
        precompute_min_distances(DS, "LID", "LID", cache_dir=tmp_path)


def test_distance_over_time_matches_hand_calculation():
    a, b = make_residue_key(DS, "4AKE", 157, "LYS"), make_residue_key(DS, "4AKE", 36, "ARG")
    res = distance_over_time(DS, a, b)
    np.testing.assert_allclose(res["distance"], hand_min_distance(*NEAR), atol=1e-4)
    assert res["frames"].tolist() == list(range(98))
    assert res["time_ns"] is None


def test_distance_over_time_rejects_wrong_resname():
    with pytest.raises(ValueError):
        distance_over_time(DS, make_residue_key(DS, "4AKE", 157, "GLY"), make_residue_key(DS, "4AKE", 36, "ARG"))


@pytest.mark.slow
def test_equilibrium_precompute_matches_direct_on_a_few_frames():
    eq = "adk_equilibrium"
    md = precompute_min_distances(eq, "LID", "NMP")
    u = load_universe(eq)
    lid = u.select_atoms("resid 122:159 and not name H*")
    nmp = u.select_atoms("resid 30:59 and not name H*")
    for frame in (0, 1000, 4186):
        u.trajectory[frame]
        for i, ra in enumerate(lid.residues):
            pa = ra.atoms.select_atoms("not name H*").positions.astype(np.float64)
            for j, rb in enumerate(nmp.residues):
                pb = rb.atoms.select_atoms("not name H*").positions
                direct = np.sqrt(((pa[:, None] - pb[None]) ** 2).sum(-1)).min()
                assert md.min_dist[frame, i, j] == pytest.approx(direct, abs=1e-3)


def test_contact_difference_map_is_window_b_minus_window_a():
    from app.science.contacts import contact_difference_map

    d = contact_difference_map(DS, "LID", "NMP", (0, 49), (49, 98))
    a = calculate_contacts(DS, "LID", "NMP", 4.5, 0, 49)
    b = calculate_contacts(DS, "LID", "NMP", 4.5, 49, 98)
    assert np.allclose(d["delta"], b["frequency"] - a["frequency"])
    assert d["keys_a"] == a["keys_a"] and d["stats_valid"] is False
    i = d["keys_a"].index(make_residue_key(DS, "4AKE", CONTACT[0], "ASP"))
    j = d["keys_b"].index(make_residue_key(DS, "4AKE", CONTACT[1], "ARG"))
    assert d["delta"][i, j] < 0  # the closed-state salt bridge breaks as the path opens
    with pytest.raises(ValueError):
        contact_difference_map(DS, "LID", "NMP", (0, 60), (40, 98))
