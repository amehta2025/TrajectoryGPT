"""Time-series statistics for 0/1 contact series. Pure NumPy/SciPy (SPEC §8a).

- tau: integrated autocorrelation time, tau = 1/2 + sum_t rho(t), Sokal automatic window
  (smallest M with M >= SOKAL_C * tau(M)). White noise gives tau = 0.5, so N_eff = N / (2 tau) = N.
- Moving-block bootstrap with block length max(1, ceil(2 tau)).
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
TOO_FEW_EVENTS = "too few events for reliable CI"


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


def describe_series(x: np.ndarray, rng: np.random.Generator, n_resamples: int = N_RESAMPLES) -> dict:
    """Frequency, tau, N_eff and block-bootstrap CI for one 0/1 series."""
    x = np.asarray(x, dtype=np.float64)
    tau = autocorr_time(x)
    transitions = count_transitions(x)
    blen = block_length(tau)
    if tau is None:
        ci = (float(x[0]), float(x[0]))
        note = "constant series (always in contact)" if x[0] else "constant series (never in contact)"
    else:
        ci = _percentile_ci(block_bootstrap_means(x, blen, rng, n_resamples))
        note = TOO_FEW_EVENTS if transitions < MIN_TRANSITIONS else None
    return {
        "frequency": float(x.mean()),
        "n": len(x),
        "tau": tau,
        "n_eff": n_effective(len(x), tau),
        "block_length": blen,
        "transitions": transitions,
        "too_few_events": transitions < MIN_TRANSITIONS,
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
    x_a: np.ndarray, x_b: np.ndarray, rng: np.random.Generator, n_resamples: int = N_RESAMPLES
) -> dict:
    """Difference of means (b - a) between two independent windows, block bootstrap each."""
    tau_a, tau_b = autocorr_time(x_a), autocorr_time(x_b)
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
        "tau_a": tau_a,
        "tau_b": tau_b,
        "n_eff_a": n_effective(len(x_a), tau_a),
        "n_eff_b": n_effective(len(x_b), tau_b),
        "transitions": transitions,
        "too_few_events": transitions < MIN_TRANSITIONS,
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


def is_supported(q: float, ci_low: float, ci_high: float, too_few_events: bool) -> bool:
    """Supported = BH q < FDR and percentile CI excludes 0 and enough events (SPEC §8a)."""
    return bool(q < FDR and (ci_low > 0.0 or ci_high < 0.0) and not too_few_events)
