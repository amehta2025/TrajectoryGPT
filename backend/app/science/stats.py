"""Time-series statistics for 0/1 contact series. Pure NumPy/SciPy (SPEC §8a).

- tau: integrated autocorrelation time, tau = 1/2 + sum_t rho(t), Sokal automatic window
  (smallest M with M >= SOKAL_C * tau(M)). White noise gives tau = 0.5, so N_eff = N / (2 tau) = N.
- tau is taken from both the 0/1 indicator and the underlying min-distance series, and the larger
  one is used: a distance drifting slowly near the cutoff makes the indicator flicker fast, so
  indicator-tau alone underestimates the memory and gives CIs that are too narrow.
- Moving-block bootstrap with block length max(1, ceil(2 tau)). With fewer than MIN_BLOCKS blocks per
  window the bootstrap itself underestimates the spread, so such series are flagged.
- Between-window differences: each window resampled independently with its own block length.
  p-value from a two-sided z-test on the bootstrap SE; CI from bootstrap percentiles.
  (A bootstrap-count p-value cannot go below ~1/n_resamples, far above BH thresholds when
  thousands of pairs are tested; see SPEC §8a.)
- Benjamini-Hochberg q-values.
"""
import math

import numpy as np
from scipy.special import erfc

SOKAL_C = 5.0
N_RESAMPLES = 2000
SEED = 0
MIN_TRANSITIONS = 5
CI_LEVEL = 0.95
FDR = 0.05
MIN_BLOCKS = 10
PROVISIONAL_MIN_BLOCKS = 20  # below this, a supported result is labelled provisional (SPEC §8a)
TOO_FEW_EVENTS = "too few events for reliable CI"
TOO_FEW_BLOCKS = f"too few independent blocks (< {MIN_BLOCKS}) for a reliable bootstrap CI"


def autocorrelation(x: np.ndarray) -> np.ndarray:
    """Normalised autocorrelation rho(t), t = 0..N-1, via FFT. Undefined for constant x."""
    x = np.asarray(x, dtype=np.float64)
    n = len(x)
    d = x - x.mean()
    f = np.fft.rfft(d, n=2 * n)
    acov = np.fft.irfft(f * np.conj(f))[:n]
    return acov / acov[0]


def autocorr_time(x: np.ndarray) -> float | None:
    """Integrated autocorrelation time in frames, or None for a constant series."""
    x = np.asarray(x, dtype=np.float64)
    if len(x) < 2 or np.all(x == x[0]):
        return None
    taus = np.cumsum(autocorrelation(x)) - 0.5  # tau(M) = 1/2 + sum_{t=1..M} rho(t)
    window = np.arange(len(taus)) >= SOKAL_C * taus
    m = int(np.argmax(window)) if window.any() else len(taus) - 1
    return float(max(taus[m], 0.5))


def series_tau(x: np.ndarray, distance: np.ndarray | None = None) -> dict:
    """tau of the 0/1 series and of its distance series; the larger one governs (None if x is constant)."""
    tau_indicator = autocorr_time(x)
    tau_distance = None if distance is None else autocorr_time(distance)
    tau = None if tau_indicator is None else max(tau_indicator, tau_distance or 0.0)
    return {"tau": tau, "tau_indicator": tau_indicator, "tau_distance": tau_distance}


def n_blocks(n: int, tau: float | None) -> float | None:
    """Bootstrap blocks per window, N / block length (None for a constant series)."""
    return None if tau is None else n / block_length(tau)


def too_few_blocks(n: int, tau: float | None) -> bool:
    return tau is not None and n_blocks(n, tau) < MIN_BLOCKS


def n_effective(n: int, tau: float | None) -> float | None:
    return None if tau is None else n / (2.0 * tau)


def block_length(tau: float | None) -> int:
    return 1 if tau is None else max(1, math.ceil(2.0 * tau))


def count_transitions(x: np.ndarray) -> int:
    x = np.asarray(x)
    return int(np.count_nonzero(x[1:] != x[:-1]))


def block_bootstrap_means(
    x: np.ndarray, block_len: int, rng: np.random.Generator, n_resamples: int = N_RESAMPLES
) -> np.ndarray:
    """Means of `n_resamples` moving-block resamples of x (blocks concatenated, truncated to N)."""
    x = np.asarray(x, dtype=np.float64)
    n = len(x)
    block_len = min(block_len, n)
    k = math.ceil(n / block_len)
    last = n - (k - 1) * block_len  # length kept from the final block
    cs = np.concatenate(([0.0], np.cumsum(x)))
    starts = rng.integers(0, n - block_len + 1, size=(n_resamples, k))
    sums = cs[starts[:, :-1] + block_len] - cs[starts[:, :-1]]
    tail = cs[starts[:, -1] + last] - cs[starts[:, -1]]
    return (sums.sum(axis=1) + tail) / n


def _percentile_ci(samples: np.ndarray) -> tuple[float, float]:
    alpha = (1.0 - CI_LEVEL) / 2.0
    lo, hi = np.quantile(samples, [alpha, 1.0 - alpha])
    return float(lo), float(hi)


def describe_series(
    x: np.ndarray, rng: np.random.Generator, n_resamples: int = N_RESAMPLES, distance: np.ndarray | None = None
) -> dict:
    """Frequency, tau, N_eff and block-bootstrap CI for one 0/1 series (and its distance series, if given)."""
    x = np.asarray(x, dtype=np.float64)
    taus = series_tau(x, distance)
    tau = taus["tau"]
    transitions = count_transitions(x)
    blen = block_length(tau)
    few_events = transitions < MIN_TRANSITIONS
    few_blocks = too_few_blocks(len(x), tau)
    if tau is None:
        ci = (float(x[0]), float(x[0]))
        note = "constant series (always in contact)" if x[0] else "constant series (never in contact)"
    else:
        ci = _percentile_ci(block_bootstrap_means(x, blen, rng, n_resamples))
        note = "; ".join(m for m, flag in ((TOO_FEW_EVENTS, few_events), (TOO_FEW_BLOCKS, few_blocks)) if flag) or None
    return {
        "frequency": float(x.mean()),
        "n": len(x),
        **taus,
        "n_eff": n_effective(len(x), tau),
        "block_length": blen,
        "n_blocks": n_blocks(len(x), tau),
        "transitions": transitions,
        "too_few_events": few_events,
        "too_few_blocks": few_blocks,
        "ci_low": ci[0],
        "ci_high": ci[1],
        "stats_note": note,
    }


def z_test_p(delta: float, se: float) -> float:
    """Two-sided normal p-value for delta / se."""
    if se == 0.0:
        return 1.0 if delta == 0.0 else 0.0
    return float(erfc(abs(delta / se) / math.sqrt(2.0)))


def compare_series(
    x_a: np.ndarray,
    x_b: np.ndarray,
    rng: np.random.Generator,
    n_resamples: int = N_RESAMPLES,
    distance_a: np.ndarray | None = None,
    distance_b: np.ndarray | None = None,
) -> dict:
    """Difference of means (b - a) between two independent windows, block bootstrap each."""
    taus_a, taus_b = series_tau(x_a, distance_a), series_tau(x_b, distance_b)
    tau_a, tau_b = taus_a["tau"], taus_b["tau"]
    boot_a = block_bootstrap_means(x_a, block_length(tau_a), rng, n_resamples)
    boot_b = block_bootstrap_means(x_b, block_length(tau_b), rng, n_resamples)
    diffs = boot_b - boot_a
    delta = float(np.mean(x_b) - np.mean(x_a))
    se = float(diffs.std(ddof=1))
    lo, hi = _percentile_ci(diffs)
    transitions = count_transitions(x_a) + count_transitions(x_b)
    return {
        "freq_a": float(np.mean(x_a)),
        "freq_b": float(np.mean(x_b)),
        "delta": delta,
        "se": se,
        "ci_low": lo,
        "ci_high": hi,
        "p": z_test_p(delta, se),
        **{f"{k}_a": v for k, v in taus_a.items()},
        **{f"{k}_b": v for k, v in taus_b.items()},
        "block_length_a": block_length(tau_a),
        "block_length_b": block_length(tau_b),
        "n_blocks_a": n_blocks(len(x_a), tau_a),
        "n_blocks_b": n_blocks(len(x_b), tau_b),
        "n_eff_a": n_effective(len(x_a), tau_a),
        "n_eff_b": n_effective(len(x_b), tau_b),
        "transitions": transitions,
        "too_few_events": transitions < MIN_TRANSITIONS,
        "too_few_blocks": too_few_blocks(len(x_a), tau_a) or too_few_blocks(len(x_b), tau_b),
    }


def bh_qvalues(p: np.ndarray) -> np.ndarray:
    """Benjamini-Hochberg adjusted p-values (q-values), same order as p."""
    p = np.asarray(p, dtype=np.float64)
    m = len(p)
    if m == 0:
        return p.copy()
    order = np.argsort(p)
    ranked = p[order] * m / np.arange(1, m + 1)
    q_sorted = np.minimum.accumulate(ranked[::-1])[::-1]
    q = np.empty(m)
    q[order] = np.minimum(q_sorted, 1.0)
    return q


def is_supported(q: float, ci_low: float, ci_high: float, too_few_events: bool, too_few_blocks: bool = False) -> bool:
    """Supported = BH q < FDR and percentile CI excludes 0 and enough events and blocks (SPEC §8a)."""
    return bool(q < FDR and (ci_low > 0.0 or ci_high < 0.0) and not too_few_events and not too_few_blocks)
