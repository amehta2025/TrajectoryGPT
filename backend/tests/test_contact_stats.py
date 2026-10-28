"""compare_intervals / contact_statistics: refusal, pair filtering, and a known strong change."""
import numpy as np
import pytest

import app.science.contact_stats as cs
from app.science import stats
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
    assert r["n_supported_firm"] + r["n_supported_provisional"] == r["n_supported"]


def test_provisional_rule(pretend_stats_valid, cache):
    r = cs.compare_intervals(DS, "NMP", "CORE", (0, 49), (49, 98), n_resamples=100, cache_dir=cache)
    firm = r["min_blocks_firm"]
    assert firm == stats.MIN_BLOCKS_FIRM
    for row in r["rows"]:
        few = any(c is not None and c < firm for c in (row["n_blocks_a"], row["n_blocks_b"]))
        assert row["provisional"] == few
        if row["tau_a"] is not None:
            assert row["n_blocks_a"] == pytest.approx(49 / (2 * row["tau_a"]))
    assert r["n_provisional"] == sum(x["provisional"] for x in r["rows"])


def test_supported_uses_tau_ci_not_bootstrap(pretend_stats_valid, cache):
    r = cs.compare_intervals(DS, "NMP", "CORE", (0, 49), (49, 98), n_resamples=100, cache_dir=cache)
    for row in r["rows"]:
        assert row["ci_low"] == pytest.approx(max(-1.0, row["delta"] - stats.Z_CI * row["se"]))
        assert "boot_ci_low" in row and "boot_se" in row  # cross-check only


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
    assert r["tau_ns"] is None
    assert r["provisional"] == (r["n_blocks"] is not None and r["n_blocks"] < stats.MIN_BLOCKS_FIRM)


@pytest.mark.slow
def test_equilibrium_known_strong_change_is_detected():
    """HSE134-ASP118 (LID-CORE) drops from ~0.53 to ~0.40 between halves, with hundreds of chunks: supported, firm."""
    eq = "adk_equilibrium"
    r = cs.compare_intervals(eq, "LID", "CORE", (0, 2093), (2093, 4187))
    row = next(x for x in r["rows"] if x["key_a"].endswith(":134:HSE") and x["key_b"].endswith(":118:ASP"))
    assert row["delta"] < -0.1 and row["supported"] and not row["provisional"]
    assert row["tau_ns_a"] == pytest.approx(row["tau_a"] * 0.24)
    assert r["n_supported_firm"] + r["n_supported_provisional"] == r["n_supported"]
    assert 0 < r["n_supported"] < r["n_tested"]  # neither all noise nor everything significant


@pytest.mark.slow
def test_equilibrium_slow_large_change_is_not_overclaimed():
    """SER30-ASP84 (NMP-CORE): large drop (0.46 -> 0.15) but only ~18-25 independent chunks per half.
    The old 2-tau bootstrap called it supported; calibrated CIs plus FDR do not (SPEC §8a)."""
    eq = "adk_equilibrium"
    r = cs.compare_intervals(eq, "NMP", "CORE", (0, 2093), (2093, 4187))
    row = next(x for x in r["rows"] if x["key_a"].endswith(":30:SER") and x["key_b"].endswith(":84:ASP"))
    assert row["delta"] < -0.2
    assert row["provisional"] and not row["supported"] and row["q"] >= 0.05


@pytest.mark.slow
def test_equilibrium_contact_statistics():
    eq = "adk_equilibrium"
    a, b = make_residue_key(eq, "ADK", 40, "LYS"), make_residue_key(eq, "ADK", 74, "GLN")
    r = cs.contact_statistics(eq, a, b)
    assert r["frequency"] == pytest.approx(0.477, abs=0.01)  # notes/recon.md
    assert r["tau"] > 0.5 and r["n_eff"] < r["n"]
    assert r["ci_low"] < r["frequency"] < r["ci_high"]
