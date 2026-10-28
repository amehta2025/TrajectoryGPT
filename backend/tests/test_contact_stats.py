"""compare_intervals / contact_statistics: refusal, pair filtering, and a known strong change."""
import numpy as np
import pytest

import app.science.contact_stats as cs
from app.config import dataset_config
from app.science.residues import make_residue_key

DS = "adk_transition"


@pytest.fixture(scope="module")
def cache(tmp_path_factory):
    return tmp_path_factory.mktemp("cache")


@pytest.fixture
def pretend_stats_valid(monkeypatch):
    """Exercise the pipeline on the small transition set (its real config refuses stats)."""
    real = dataset_config(DS)
    monkeypatch.setattr(cs, "dataset_config", lambda ds: {**real, "stats_valid": True})


def test_compare_refuses_on_transition(cache):
    with pytest.raises(cs.StatsRefused, match="biased"):
        cs.compare_intervals(DS, "LID", "NMP", (0, 49), (49, 98), cache_dir=cache)


def test_contact_statistics_refuses_on_transition():
    a, b = make_residue_key(DS, "4AKE", 157, "LYS"), make_residue_key(DS, "4AKE", 36, "ARG")
    with pytest.raises(cs.StatsRefused):
        cs.contact_statistics(DS, a, b)


def test_compare_filters_and_counts(pretend_stats_valid, cache):
    r = cs.compare_intervals(DS, "NMP", "CORE", (0, 49), (49, 98), n_resamples=200, cache_dir=cache)
    assert r["n_pairs"] == 30 * 146 - 6  # minus the sequence-local pairs
    assert r["n_tested"] + r["n_filtered"] == r["n_pairs"]
    assert 0 < r["n_tested"] < r["n_pairs"]
    for row in r["rows"]:
        pooled = (row["freq_a"] * 49 + row["freq_b"] * 49) / 98
        assert pooled >= cs.MIN_POOLED_FREQ
        assert row["delta"] == pytest.approx(row["freq_b"] - row["freq_a"])
        assert 0 <= row["q"] <= 1 and row["q"] >= row["p"] - 1e-12
        if row["supported"]:
            assert not row["too_few_events"] and row["q"] < 0.05
            assert row["ci_low"] > 0 or row["ci_high"] < 0
        assert row["tau_ns_a"] is None  # no physical time on the transition path
    assert r["n_supported"] == sum(x["supported"] for x in r["rows"])


def test_compare_is_reproducible(pretend_stats_valid, cache):
    kw = dict(n_resamples=100, cache_dir=cache)
    a = cs.compare_intervals(DS, "LID", "NMP", (0, 40), (50, 98), **kw)
    b = cs.compare_intervals(DS, "LID", "NMP", (0, 40), (50, 98), **kw)
    assert a["rows"] == b["rows"]


@pytest.mark.parametrize("wa,wb", [((0, 60), (50, 98)), ((0, 10), (5, 20)), ((0, 99), (0, 10))])
def test_compare_rejects_overlapping_or_bad_windows(pretend_stats_valid, cache, wa, wb):
    with pytest.raises(ValueError):
        cs.compare_intervals(DS, "LID", "NMP", wa, wb, cache_dir=cache)


def test_contact_statistics_pipeline(pretend_stats_valid):
    a, b = make_residue_key(DS, "4AKE", 157, "LYS"), make_residue_key(DS, "4AKE", 36, "ARG")
    r = cs.contact_statistics(DS, a, b, n_resamples=200)
    assert r["frequency"] == pytest.approx(np.mean(r["in_contact"]))
    assert r["ci_low"] <= r["frequency"] <= r["ci_high"]
    assert r["n_eff_time_ns"] is None


@pytest.mark.slow
def test_equilibrium_known_strong_change_is_detected():
    """SER30-ASP84 (NMP-CORE) drops from ~0.46 to ~0.15 between the two halves: must be supported."""
    eq = "adk_equilibrium"
    r = cs.compare_intervals(eq, "NMP", "CORE", (0, 2093), (2093, 4187))
    row = next(x for x in r["rows"] if x["key_a"].endswith(":30:SER") and x["key_b"].endswith(":84:ASP"))
    assert row["delta"] < -0.2 and row["supported"]
    assert row["n_eff_time_ns_a"] == pytest.approx(row["n_eff_a"] * 0.24)
    assert 0 < r["n_supported"] < r["n_tested"]  # neither all noise nor everything significant


@pytest.mark.slow
def test_equilibrium_contact_statistics():
    eq = "adk_equilibrium"
    a, b = make_residue_key(eq, "ADK", 40, "LYS"), make_residue_key(eq, "ADK", 74, "GLN")
    r = cs.contact_statistics(eq, a, b)
    assert r["frequency"] == pytest.approx(0.477, abs=0.01)  # notes/recon.md
    assert r["tau"] > 0.5 and r["n_eff"] < r["n"]
    assert r["ci_low"] < r["frequency"] < r["ci_high"]
