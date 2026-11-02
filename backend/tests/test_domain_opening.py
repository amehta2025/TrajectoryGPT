"""Domain opening (SPEC §10b c): COM distance to CORE per frame, checked by hand on adk_transition."""
import json
import warnings

import MDAnalysis as mda
import numpy as np
import pytest

from app.agent.tools import ApprovedPlan, RunState, run_tool
from app.config import dataset_config
from app.science.domain_opening import domain_opening
from app.science.loader import dataset_files

DS = "adk_transition"


def _com(atoms):
    m = atoms.masses
    return (atoms.positions * m[:, None]).sum(axis=0) / m.sum()


def test_matches_a_hand_calculation_on_three_frames():
    r = domain_opening(DS, "LID")
    assert r["n_frames"] == 98 and r["time_ns"] is None and r["units"]["distance"] == "Å"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        u = mda.Universe(*dataset_files(DS))
    ranges = dataset_config(DS)["domains"]
    sel = lambda g: u.select_atoms("protein and (" + " or ".join(f"resid {a}:{b}" for a, b in ranges[g]) + ")")
    lid, core = sel("LID"), sel("CORE")
    for f in (0, 49, 97):
        u.trajectory[f]
        assert r["distance"][f] == pytest.approx(np.linalg.norm(_com(lid) - _com(core)), abs=1e-4)


def test_lid_opens_along_the_closed_to_open_path():
    d = domain_opening(DS, "LID")["distance"]
    assert d[-1] > d[0] + 3  # the LID swings away from CORE on the way to the open state
    nmp = domain_opening(DS, "NMP", 10, 20, 2)
    assert list(nmp["frames"]) == [10, 12, 14, 16, 18] and "biased" in nmp["note"]


def test_core_and_unknown_domains_are_rejected():
    for bad in ("CORE", "ATP"):
        with pytest.raises(ValueError, match="domain must be one of"):
            domain_opening(DS, bad)


def _state(a="LID", b="CORE"):
    steps = [{"tool": "domain_opening", "rationale": ""}, {"tool": "submit_report", "rationale": ""}]
    return RunState(ApprovedPlan(DS, a, b, 4.5, [0, 49], [49, 98], steps=steps))


def test_tool_uses_the_approved_group_and_gives_placeholders():
    s = _state()
    r = run_tool(s, "domain_opening", json.dumps({"window": "all"}))
    assert r["status"] == "ok"
    summary = json.loads(r["summary_for_model"])
    assert summary["domain"] == "LID" and summary["last_A"] > summary["first_A"]
    assert "not statistically tested" in summary["note"]
    values = s.results[r["provenance_id"]]["values"]
    assert {"LID-CORE.min_distance", "LID-CORE.max_distance", "LID-CORE.frame_of_max"} <= set(values)


def test_tool_refuses_a_domain_outside_the_approved_groups():
    r = run_tool(_state(), "domain_opening", json.dumps({"domain": "NMP"}))
    assert r["status"] == "refused" and "approved groups" in r["summary_for_model"]
