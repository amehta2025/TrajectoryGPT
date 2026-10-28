"""Residue keys: `dataset:segid:resid:resname`. Build and parse only here."""
from typing import NamedTuple


class ResidueKey(NamedTuple):
    dataset: str
    segid: str
    resid: int
    resname: str


def make_residue_key(dataset: str, segid: str, resid: int, resname: str) -> str:
    for name, part in (("dataset", dataset), ("segid", segid), ("resname", resname)):
        if ":" in part:
            raise ValueError(f"{name} may not contain ':': {part!r}")
    return f"{dataset}:{segid}:{int(resid)}:{resname}"


def parse_residue_key(key: str) -> ResidueKey:
    parts = key.split(":")
    if len(parts) != 4:
        raise ValueError(f"Residue key must be dataset:segid:resid:resname, got {key!r}")
    dataset, segid, resid, resname = parts
    try:
        resid_int = int(resid)
    except ValueError:
        raise ValueError(f"resid must be an integer in {key!r}") from None
    return ResidueKey(dataset, segid, resid_int, resname)


def residue_keys(dataset: str, residues) -> list[str]:
    """Keys for an MDAnalysis ResidueGroup, in order."""
    return [make_residue_key(dataset, r.segid, r.resid, r.resname) for r in residues]
