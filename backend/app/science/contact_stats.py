"""Contact statistics on top of the min-distance precompute: compare_intervals, contact_statistics.

Both refuse on datasets with `stats_valid: false` (SPEC §8a).
"""
from pathlib import Path
from typing import Callable

import numpy as np

from app.config import CACHE_DIR, dataset_config
from app.science import stats
from app.science.contacts import (
    CONTACT_DEFINITION, precompute_min_distances, validate_cutoff,
    validate_sequence_exclusion, validate_window, sequence_local_mask,
)
from app.science.distances import distance_over_time
from app.science.residues import parse_residue_key

MIN_POOLED_FREQ = 0.01  # pairs below this pooled frequency are not tested (rare or never in contact)
SLOW_QUANTILE = 0.75  # tau_distance at or above this quantile of the tested pairs counts as slow
PROVISIONAL_FEW_BLOCKS = f"fewer than {stats.PROVISIONAL_MIN_BLOCKS} bootstrap blocks in a window"
PROVISIONAL_SLOW = "tau_distance in the slowest quartile of tested pairs"


class StatsRefused(Exception):
    """Statistics are not meaningful on this dataset; str(e) is the user-facing reason."""


def require_stats_valid(dataset: str) -> dict:
    cfg = dataset_config(dataset)
    if not cfg["stats_valid"]:
        raise StatsRefused(cfg["stats_refusal"].strip())
    return cfg


def _pair_rng(seed: int, key_a: str, key_b: str) -> np.random.Generator:
    """One generator per pair, so a pair's CI does not depend on which other pairs were tested."""
    return np.random.default_rng([seed, parse_residue_key(key_a).resid, parse_residue_key(key_b).resid])


def _tau_ns(tau: float | None, dt_ns: float | None, stride: int) -> float | None:
    """tau in ns where physical time exists (one sample = stride * dt_ns); None on the transition path."""
    return None if tau is None or dt_ns is None else tau * dt_ns * stride


def _few_blocks_for_provisional(*blocks: float | None) -> bool:
    return any(b is not None and b < stats.PROVISIONAL_MIN_BLOCKS for b in blocks)


def _mark_provisional(rows: list[dict]) -> float | None:
    """Label rows provisional (few blocks or slowest-quartile tau_distance). Returns the slow threshold."""
    slow_tau = [max(t for t in (r["tau_distance_a"], r["tau_distance_b"]) if t is not None)
                for r in rows if r["tau_distance_a"] is not None or r["tau_distance_b"] is not None]
    threshold = float(np.quantile(slow_tau, SLOW_QUANTILE)) if slow_tau else None
    for r in rows:
        reasons = []
        if _few_blocks_for_provisional(r["n_blocks_a"], r["n_blocks_b"]):
            reasons.append(PROVISIONAL_FEW_BLOCKS)
        td = [t for t in (r["tau_distance_a"], r["tau_distance_b"]) if t is not None]
        if threshold is not None and td and max(td) >= threshold:
            reasons.append(PROVISIONAL_SLOW)
        r["provisional"] = bool(reasons)
        r["provisional_reasons"] = reasons
    return threshold


def compare_intervals(
    dataset: str,
    group_a: str,
    group_b: str,
    window_a: tuple[int, int],
    window_b: tuple[int, int],
    cutoff: float = 4.5,
    stride: int = 1,
    sequence_exclusion: int | None = None,
    *,
    seed: int = stats.SEED,
    n_resamples: int = stats.N_RESAMPLES,
    min_pooled_freq: float = MIN_POOLED_FREQ,
    cache_dir: Path = CACHE_DIR,
    progress: Callable[[str], None] | None = None,
) -> dict:
    """Per-pair contact-frequency change (window_b - window_a) with block-bootstrap CI and BH q-values.

    Tested pairs: not sequence-local and pooled frequency over both windows >= min_pooled_freq.
    tau (hence block length) is the larger of the 0/1 series' and the min-distance series' tau.
    Supported = q < FDR and CI excludes 0 and not too few events and not too few blocks.
    Provisional = < 20 blocks in a window or tau_distance in the slowest quartile of tested pairs;
    a supported provisional pair is shown as "supported (slow — provisional)".
    """
    cfg = require_stats_valid(dataset)
    cutoff = validate_cutoff(cutoff)
    sequence_exclusion = validate_sequence_exclusion(sequence_exclusion)
    md = precompute_min_distances(dataset, group_a, group_b, cache_dir=cache_dir, progress=progress)
    n_frames = md.min_dist.shape[0]
    fa = validate_window(n_frames, window_a[0], window_a[1], stride)
    fb = validate_window(n_frames, window_b[0], window_b[1], stride)
    if fa.start < fb.stop and fb.start < fa.stop:
        raise ValueError(f"Windows {list(window_a)} and {list(window_b)} overlap; choose disjoint windows.")

    dist_a = md.min_dist[fa.start:fa.stop:fa.step]
    dist_b = md.min_dist[fb.start:fb.stop:fb.step]
    in_a, in_b = dist_a <= cutoff, dist_b <= cutoff
    excluded = sequence_local_mask(md.keys_a, md.keys_b, sequence_exclusion)
    pooled = (in_a.sum(axis=0) + in_b.sum(axis=0)) / (len(fa) + len(fb))
    candidates = ~excluded
    tested = candidates & (pooled >= min_pooled_freq)

    rows = []
    for i, j in zip(*np.nonzero(tested)):
        ka, kb = md.keys_a[i], md.keys_b[j]
        r = stats.compare_series(in_a[:, i, j], in_b[:, i, j], _pair_rng(seed, ka, kb), n_resamples,
                                 dist_a[:, i, j], dist_b[:, i, j])
        rows.append({"key_a": ka, "key_b": kb, **r})
    q = stats.bh_qvalues(np.array([r["p"] for r in rows]))
    for r, qi in zip(rows, q):
        r["q"] = float(qi)
        r["supported"] = stats.is_supported(r["q"], r["ci_low"], r["ci_high"], r["too_few_events"], r["too_few_blocks"])
        r["tau_ns_a"] = _tau_ns(r["tau_a"], cfg["dt_ns"], stride)
        r["tau_ns_b"] = _tau_ns(r["tau_b"], cfg["dt_ns"], stride)
    slow_threshold = _mark_provisional(rows)
    rows.sort(key=lambda r: (not r["supported"], r["q"], -abs(r["delta"])))

    return {
        "dataset": dataset,
        "group_a": group_a,
        "group_b": group_b,
        "window_a": list(window_a),
        "window_b": list(window_b),
        "stride": stride,
        "n_frames_a": len(fa),
        "n_frames_b": len(fb),
        "cutoff": cutoff,
        "sequence_exclusion": sequence_exclusion,
        "contact_definition": CONTACT_DEFINITION,
        "rows": rows,
        "n_pairs": int(candidates.sum()),
        "n_tested": len(rows),
        "n_filtered": int(candidates.sum() - len(rows)),
        "n_supported": sum(r["supported"] for r in rows),
        "n_supported_firm": sum(r["supported"] and not r["provisional"] for r in rows),
        "n_supported_provisional": sum(r["supported"] and r["provisional"] for r in rows),
        "n_provisional": sum(r["provisional"] for r in rows),
        "provisional_rule": {
            "min_blocks": stats.PROVISIONAL_MIN_BLOCKS,
            "slow_quantile": SLOW_QUANTILE,
            "slow_tau_distance_frames": slow_threshold,
            "slow_tau_distance_ns": _tau_ns(slow_threshold, cfg["dt_ns"], stride),
        },
        "n_too_few_blocks": sum(r["too_few_blocks"] for r in rows),
        "min_pooled_freq": min_pooled_freq,
        "fdr": stats.FDR,
        "ci_level": stats.CI_LEVEL,
        "seed": seed,
        "n_resamples": n_resamples,
        "pbc": md.pbc,
        "units": {"distance": "Å", "time": cfg["time_label"], "dt_ns": cfg["dt_ns"]},
    }


def contact_statistics(
    dataset: str,
    key_a: str,
    key_b: str,
    cutoff: float = 4.5,
    start: int = 0,
    stop: int | None = None,
    stride: int = 1,
    *,
    seed: int = stats.SEED,
    n_resamples: int = stats.N_RESAMPLES,
) -> dict:
    """tau, N_eff and block-bootstrap CI of one pair's contact frequency."""
    cfg = require_stats_valid(dataset)
    cutoff = validate_cutoff(cutoff)
    d = distance_over_time(dataset, key_a, key_b, start, stop, stride)
    series = d["distance"] <= cutoff
    r = stats.describe_series(series, _pair_rng(seed, key_a, key_b), n_resamples, distance=d["distance"])
    return {
        "dataset": dataset,
        "key_a": key_a,
        "key_b": key_b,
        "cutoff": cutoff,
        "window": {"start": start, "stop": stop, "stride": stride},
        "frames": d["frames"],
        "in_contact": series,
        **r,
        "tau_ns": _tau_ns(r["tau"], cfg["dt_ns"], stride),
        # One pair has no quartile to compare against, so only the block rule applies here.
        "provisional": _few_blocks_for_provisional(r["n_blocks"]),
        "contact_definition": CONTACT_DEFINITION,
        "seed": seed,
        "n_resamples": n_resamples,
        "pbc": d["pbc"],
        "units": {"distance": "Å", "time": cfg["time_label"], "dt_ns": cfg["dt_ns"]},
    }
