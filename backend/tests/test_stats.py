"""Validation tests for stats.py (SPEC §8a), on synthetic series with known answers."""
import numpy as np
import pytest
from scipy.stats import false_discovery_control

from app.science import stats


def ar1(n: int, phi: float, rng: np.random.Generator) -> np.ndarray:
    x = np.empty(n)
    x[0] = rng.standard_normal()
    noise = rng.standard_normal(n) * np.sqrt(1 - phi**2)
    for t in range(1, n):
        x[t] = phi * x[t - 1] + noise[t]
    return x


def binary_ar1(n: int, phi: float, p_on: float, rng: np.random.Generator) -> np.ndarray:
    """Correlated 0/1 series: AR(1) thresholded so a fraction p_on is 'in contact'."""
    x = ar1(n, phi, rng)
    return (x > np.quantile(x, 1 - p_on)).astype(float)


def naive_ci_width(x: np.ndarray) -> float:
    p = x.mean()
    return 2 * 1.96 * np.sqrt(p * (1 - p) / len(x))


@pytest.mark.parametrize("phi", [0.5, 0.8, 0.9])
def test_tau_on_ar1_within_20_percent(phi):
    true_tau = (1 + phi) / (2 * (1 - phi))  # 1/2 + sum_t phi^t
    est = stats.autocorr_time(ar1(50_000, phi, np.random.default_rng(1)))
    assert est == pytest.approx(true_tau, rel=0.2)


def test_white_noise_tau_and_n_eff():
    x = np.random.default_rng(2).integers(0, 2, 5000).astype(float)
    tau = stats.autocorr_time(x)
    assert tau == pytest.approx(0.5, abs=0.1)
    assert stats.n_effective(len(x), tau) == pytest.approx(5000, rel=0.2)
    assert stats.block_length(tau) <= 2  # ceil(2 tau) with tau estimated near 0.5


def test_tau_se_formula():
    assert stats.tau_se(0.3, 1000, 0.5) == pytest.approx(np.sqrt(0.3 * 0.7 / 1000))  # white noise = naive SE
    assert stats.tau_se(0.3, 1000, 8.0) == pytest.approx(np.sqrt(0.3 * 0.7 * 16 / 1000))
    assert stats.tau_se(1.0, 1000, None) == 0.0


def test_compare_se_combines_windows():
    rng = np.random.default_rng(13)
    a, b = binary_ar1(2000, 0.9, 0.3, rng), binary_ar1(2000, 0.9, 0.6, rng)
    r = stats.compare_series(a, b, np.random.default_rng(0), n_resamples=100)
    se_a = stats.tau_se(a.mean(), len(a), r["tau_a"])
    se_b = stats.tau_se(b.mean(), len(b), r["tau_b"])
    assert r["se"] == pytest.approx(np.hypot(se_a, se_b))
    assert r["ci_low"] == pytest.approx(r["delta"] - stats.Z_CI * r["se"])
    assert r["n_blocks_a"] == pytest.approx(2000 / (2 * r["tau_a"]))


def test_tau_ci_coverage_on_correlated_series():
    """A small version of scripts/calibrate_ci.py: AR(1) thresholded at 0 has true frequency exactly 0.5."""
    rng = np.random.default_rng(14)
    hits = 0
    for _ in range(300):
        x = (ar1(2000, 0.9, rng) > 0).astype(float)
        d = stats.describe_series(x, np.random.default_rng(0), n_resamples=20)
        hits += d["ci_low"] <= 0.5 <= d["ci_high"]
    assert hits / 300 >= 0.90


def test_white_noise_block_ci_matches_naive():
    x = np.random.default_rng(3).binomial(1, 0.3, 4000).astype(float)
    d = stats.describe_series(x, np.random.default_rng(stats.SEED))
    assert d["ci_high"] - d["ci_low"] == pytest.approx(naive_ci_width(x), rel=0.15)
    assert d["ci_low"] < x.mean() < d["ci_high"]


def test_correlated_block_ci_is_clearly_wider():
    x = binary_ar1(4000, 0.95, 0.4, np.random.default_rng(4))
    d = stats.describe_series(x, np.random.default_rng(stats.SEED))
    assert d["tau"] > 5
    assert d["ci_high"] - d["ci_low"] > 2.5 * naive_ci_width(x)


def test_bootstrap_is_reproducible_with_seed():
    x = binary_ar1(1000, 0.9, 0.5, np.random.default_rng(5))
    a = stats.describe_series(x, np.random.default_rng(7))
    b = stats.describe_series(x, np.random.default_rng(7))
    assert a == b


def test_block_bootstrap_means_shape_and_truncation():
    x = np.arange(10, dtype=float)
    m = stats.block_bootstrap_means(x, 3, np.random.default_rng(0), n_resamples=50)
    assert m.shape == (50,)
    # with block length = N every resample is the whole series
    assert np.allclose(stats.block_bootstrap_means(x, 10, np.random.default_rng(0), 5), x.mean())


@pytest.mark.parametrize("value", [0.0, 1.0])
def test_constant_series(value):
    d = stats.describe_series(np.full(200, value), np.random.default_rng(0))
    assert d["tau"] is None and d["n_eff"] is None
    assert d["ci_low"] == d["ci_high"] == value
    assert "constant" in d["stats_note"]


def test_too_few_transitions_flagged():
    x = np.zeros(500)
    x[100:150] = 1  # two transitions
    d = stats.describe_series(x, np.random.default_rng(0))
    assert d["transitions"] == 2 and d["too_few_events"]
    assert d["stats_note"] == stats.TOO_FEW_EVENTS


def test_bh_matches_scipy():
    p = np.random.default_rng(6).uniform(size=200) ** 3
    np.testing.assert_allclose(stats.bh_qvalues(p), false_discovery_control(p, method="bh"))


def test_bh_hand_example():
    q = stats.bh_qvalues(np.array([0.01, 0.04, 0.03, 0.5]))
    # sorted p*m/rank: 0.04, 0.06, 0.0533, 0.5 -> step-up minimum: 0.04, 0.0533, 0.0533, 0.5
    np.testing.assert_allclose(q, [0.04, 0.16 / 3, 0.16 / 3, 0.5])


def test_pure_noise_pairs_bh_supports_at_most_5_percent():
    """500 pairs where both windows come from the same correlated process: no real changes."""
    rng = np.random.default_rng(8)
    boot_rng = np.random.default_rng(stats.SEED)
    rows = []
    for _ in range(500):
        p_on = rng.uniform(0.1, 0.9)
        x = binary_ar1(1600, 0.9, p_on, rng)
        rows.append(stats.compare_series(x[:800], x[800:], boot_rng, n_resamples=500))
    q = stats.bh_qvalues(np.array([r["p"] for r in rows]))
    n_supported = sum(stats.is_supported(qi, r["ci_low"], r["ci_high"], r["too_few_events"]) for qi, r in zip(q, rows))
    assert n_supported <= 25


def test_real_change_is_supported():
    rng = np.random.default_rng(9)
    a = binary_ar1(2000, 0.9, 0.2, rng)
    b = binary_ar1(2000, 0.9, 0.7, rng)
    r = stats.compare_series(a, b, np.random.default_rng(stats.SEED))
    assert r["delta"] == pytest.approx(0.5, abs=0.01)
    q = stats.bh_qvalues(np.array([r["p"], 0.5, 0.9]))[0]
    assert stats.is_supported(q, r["ci_low"], r["ci_high"], r["too_few_events"])


def test_too_few_events_never_supported():
    assert not stats.is_supported(0.0001, 0.2, 0.4, too_few_events=True)
    assert not stats.is_supported(0.0001, -0.1, 0.4, too_few_events=False)  # CI spans 0
    assert not stats.is_supported(0.2, 0.2, 0.4, too_few_events=False)  # q too large
    assert stats.is_supported(0.01, -0.4, -0.2, too_few_events=False)


def test_z_test_p():
    assert stats.z_test_p(0.0, 0.0) == 1.0
    assert stats.z_test_p(1.96, 1.0) == pytest.approx(0.05, abs=1e-3)


def slow_drift_distance(n: int, rng: np.random.Generator, cutoff: float = 4.5) -> np.ndarray:
    """Min distance drifting slowly (AR(1), tau ~ 50 frames) around the cutoff, plus fast thermal noise."""
    return cutoff + 0.4 * ar1(n, 0.98, rng) + 0.6 * rng.standard_normal(n)


def test_slow_drift_distance_widens_block_ci():
    """The indicator flickers fast while the distance drifts slowly: indicator-tau alone is too short."""
    d = slow_drift_distance(8000, np.random.default_rng(10))
    x = (d <= 4.5).astype(float)
    ind = stats.describe_series(x, np.random.default_rng(stats.SEED))
    both = stats.describe_series(x, np.random.default_rng(stats.SEED), distance=d)
    assert ind["transitions"] > 1000  # fast flicker
    assert both["tau_distance"] > 2 * both["tau_indicator"]
    assert both["tau"] == both["tau_distance"] and both["block_length"] > ind["block_length"]
    assert both["n_eff"] < ind["n_eff"]
    assert (both["ci_high"] - both["ci_low"]) > 1.3 * (ind["ci_high"] - ind["ci_low"])


def test_compare_uses_distance_tau():
    d = slow_drift_distance(4000, np.random.default_rng(11))
    x = (d <= 4.5).astype(float)
    ind = stats.compare_series(x[:2000], x[2000:], np.random.default_rng(stats.SEED))
    both = stats.compare_series(x[:2000], x[2000:], np.random.default_rng(stats.SEED),
                                distance_a=d[:2000], distance_b=d[2000:])
    assert both["tau_a"] == max(both["tau_indicator_a"], both["tau_distance_a"])
    assert both["block_length_a"] >= ind["block_length_a"] and both["block_length_b"] >= ind["block_length_b"]
    assert both["se"] > ind["se"]


def test_too_few_blocks_flagged_and_never_supported():
    rng = np.random.default_rng(12)
    x = binary_ar1(400, 0.995, 0.5, rng)  # estimated tau ~ 24 frames: about 8 blocks of 2 tau
    d = stats.describe_series(x, np.random.default_rng(0))
    assert d["too_few_blocks"] and stats.TOO_FEW_BLOCKS in d["stats_note"]
    assert not stats.is_supported(0.0001, 0.2, 0.4, too_few_events=False, too_few_blocks=True)
