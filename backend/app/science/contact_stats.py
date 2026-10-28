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


def _time(n_eff: float | None, tau: float | None, dt_ns: float | None, stride: int) -> dict:
    """tau and N_eff in ns where physical time exists (one sample = stride * dt_ns)."""
    if dt_ns is None:
        return {"tau_ns": None, "n_eff_time_ns": None}
    step = dt_ns * stride
    return {
        "tau_ns": None if tau is None else tau * step,
        "n_eff_time_ns": None if n_eff is None else n_eff * step,
    }


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
        r.update({f"{k}_a": v for k, v in _time(r["n_eff_a"], r["tau_a"], cfg["dt_ns"], stride).items()})
        r.update({f"{k}_b": v for k, v in _time(r["n_eff_b"], r["tau_b"], cfg["dt_ns"], stride).items()})
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
        **_time(r["n_eff"], r["tau"], cfg["dt_ns"], stride),
        "contact_definition": CONTACT_DEFINITION,
        "seed": seed,
        "n_resamples": n_resamples,
        "pbc": d["pbc"],
        "units": {"distance": "Å", "time": cfg["time_label"], "dt_ns": cfg["dt_ns"]},
    }
