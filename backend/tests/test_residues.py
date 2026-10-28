import pytest

from app.science.loader import load_universe
from app.science.residues import ResidueKey, make_residue_key, parse_residue_key, residue_keys


def test_round_trip():
    key = make_residue_key("adk_transition", "4AKE", 42, "GLY")
    assert key == "adk_transition:4AKE:42:GLY"
    assert parse_residue_key(key) == ResidueKey("adk_transition", "4AKE", 42, "GLY")


def test_round_trip_every_residue_in_dataset():
    u = load_universe("adk_transition")
    for key, res in zip(residue_keys("adk_transition", u.residues), u.residues):
        parsed = parse_residue_key(key)
        assert (parsed.segid, parsed.resid, parsed.resname) == (res.segid, res.resid, res.resname)
        assert make_residue_key(*parsed) == key


@pytest.mark.parametrize("bad", ["a:b:c", "a:b:c:d:e", "ds:4AKE:x:GLY", ""])
def test_parse_rejects_bad_keys(bad):
    with pytest.raises(ValueError):
        parse_residue_key(bad)


def test_make_rejects_colon():
    with pytest.raises(ValueError):
        make_residue_key("bad:name", "4AKE", 1, "MET")
