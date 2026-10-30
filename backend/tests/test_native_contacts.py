"""native_contacts must reproduce the MDAnalysis User Guide Q1 vs Q2 example exactly (SPEC §5c)."""
import MDAnalysis as mda
import numpy as np
import pytest
from MDAnalysis.analysis import contacts
from MDAnalysisTests.datafiles import DCD, PSF

from app.science.native_contacts import native_contacts

DS = "adk_transition"


@pytest.fixture(scope="module")
def user_guide_timeseries():
    """The User Guide's published code, verbatim in substance."""
    u = mda.Universe(PSF, DCD)
    q1q2 = contacts.q1q2(u, "name CA", radius=8)
    q1q2.run()
    return q1q2.results.timeseries


def test_matches_user_guide_q1q2_exactly(user_guide_timeseries):
    r = native_contacts(DS)
    assert user_guide_timeseries.shape == (98, 3)
    np.testing.assert_array_equal(r["frames"], user_guide_timeseries[:, 0])
    np.testing.assert_array_equal(r["q"], user_guide_timeseries[:, 1:])
    assert r["ref_frames"] == [0, 97]


def test_known_endpoints():
    q = native_contacts(DS)["q"]
    assert q[0, 0] == 1.0 and q[-1, 1] == 1.0  # each reference matches itself
    assert q[-1, 0] == pytest.approx(0.932, abs=1e-3)  # notes/recon.md
    assert q[0, 1] == pytest.approx(0.946, abs=1e-3)


def test_window_and_custom_reference():
    r = native_contacts(DS, ref_frames=(50,), start=10, stop=60, stride=5)
    assert r["frames"].tolist() == list(range(10, 60, 5))
    assert r["q"].shape == (10, 1)
    assert r["q"][r["frames"].tolist().index(50), 0] == 1.0
    assert r["time_ns"] is None


@pytest.mark.parametrize("kwargs", [{"radius": 1.0}, {"radius": 20.0}, {"ref_frames": (98,)}, {"ref_frames": ()},
                                    {"selection": "name XYZ"}, {"selection": "resid and"}])
def test_rejects_bad_params(kwargs):
    with pytest.raises(ValueError):
        native_contacts(DS, **kwargs)


def test_reproduction_check_matches_published_code():
    from app.science.native_contacts import reproduction_check
    r = reproduction_check(DS)
    assert r["match"] and r["max_abs_diff"] <= r["tolerance"] and r["n_values"] == 98 * 2
    assert r["endpoints"]["q1_last"] == pytest.approx(0.932, abs=1e-3)


def test_reproduction_check_detects_a_mismatch(monkeypatch):
    import app.science.native_contacts as nc
    real = nc.native_contacts
    monkeypatch.setattr(nc, "native_contacts", lambda *a, **k: {**(r := real(*a, **k)), "q": r["q"] + 1e-3})
    assert not nc.reproduction_check(DS)["match"]
