from app.config import dataset_config
from app.science.loader import group_heavy_atoms, group_selection, load_universe


def test_transition_loads():
    u = load_universe("adk_transition")
    assert u.trajectory.n_frames == 98
    assert u.residues.n_residues == 214
    assert dataset_config("adk_transition")["stats_valid"] is False


def test_domains_cover_protein_once():
    u = load_universe("adk_transition")
    resids = []
    for g in ("NMP", "LID", "CORE"):
        resids += list(u.select_atoms(group_selection("adk_transition", g)).residues.resids)
    assert sorted(resids) == list(range(1, 215))


def test_pbc_comes_from_config():
    from app.science.loader import box_for, pbc_mode
    for ds in ("adk_transition", "adk_equilibrium"):
        assert pbc_mode(ds) == "none"
    u = load_universe("adk_transition")
    assert box_for("adk_transition", u.trajectory.ts) is None


def test_heavy_atoms_have_no_hydrogens():
    ag = group_heavy_atoms("adk_transition", "LID")
    assert len(ag) > 0
    assert not any(n.startswith("H") for n in ag.names)
