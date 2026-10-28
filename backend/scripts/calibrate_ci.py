"""Coverage calibration of the contact-statistics CIs (SPEC §8a). Rerunnable.

Synthetic min-distance series with a known true contact frequency:
    distance = cutoff + offset + amp * AR(1) + noise * N(0, 1),    in contact if distance <= cutoff
so the true frequency is Phi(-offset / sqrt(amp^2 + noise^2)) and the true difference between two
windows of one stationary series is 0. For each replicate the production functions
(stats.describe_series, stats.compare_series, with the distance series) give a tau-based CI and a
block-bootstrap cross-check CI; we record whether each contains the truth, and the number of
independent chunks N / (2 tau) per window.

MIN_BLOCKS_FIRM = the smallest chunk-bin lower edge from which every bin has tau-based coverage
>= 90% for both the frequency and the difference.

Run from backend/:  python -m scripts.calibrate_ci [--reps 150] [--out ../notes/calibration]
Writes coverage_by_process.csv and coverage_by_chunks.csv, and prints the threshold.
"""
import argparse
import csv
import math
import time
from pathlib import Path

import numpy as np
from scipy.stats import norm

from app.config import REPO_ROOT
from app.science import stats

CUTOFF = 4.5
SEED = 2026
N_BOOT = 500  # cross-check only; fewer resamples than production to keep the run short
WINDOWS = (500, 1000, 2000, 4000, 8000, 16000)
CHUNK_EDGES = (0, 5, 10, 15, 20, 30, 40, 50, 75, 100, 150, 200, 300, 500, 1000, math.inf)
TARGET = 0.90

# name: (phi or None for white noise, slow amplitude, fast noise, offset of the mean from the cutoff, Å)
PROCESSES = {
    "white": (None, 0.0, 1.0, 0.0),
    "fast AR 0.8": (0.8, 1.0, 0.0, 0.0),
    "slow 0.98, noise 0.1": (0.98, 0.4, 0.1, 0.0),
    "slow 0.98, noise 0.2": (0.98, 0.4, 0.2, 0.0),
    "slow 0.98, noise 0.6": (0.98, 0.4, 0.6, 0.0),
    "very slow 0.995, noise 0.2": (0.995, 0.4, 0.2, 0.0),
    "slow 0.98, noise 0.2, p~0.23": (0.98, 0.4, 0.2, 0.33),
    "very slow 0.995, noise 0.2, p~0.23": (0.995, 0.4, 0.2, 0.33),
}


def ar1(n: int, phi: float, rng: np.random.Generator) -> np.ndarray:
    """Unit-variance AR(1)."""
    x = np.empty(n)
    x[0] = rng.standard_normal()
    noise = rng.standard_normal(n) * math.sqrt(1 - phi**2)
    for t in range(1, n):
        x[t] = phi * x[t - 1] + noise[t]
    return x


def distance_series(n: int, proc: tuple, rng: np.random.Generator) -> np.ndarray:
    phi, amp, noise, offset = proc
    slow = ar1(n, phi, rng) if phi is not None else 0.0
    return CUTOFF + offset + amp * slow + noise * rng.standard_normal(n)


def true_frequency(proc: tuple) -> float:
    _, amp, noise, offset = proc
    return float(norm.cdf(-offset / math.hypot(amp, noise)))


def run(reps: int) -> list[dict]:
    rows = []
    for k, (name, proc) in enumerate(PROCESSES.items()):
        p_true = true_frequency(proc)
        t0 = time.perf_counter()
        for n in WINDOWS:
            rng = np.random.default_rng([SEED, k, n])
            for _ in range(reps):
                d = distance_series(2 * n, proc, rng)
                x = (d <= CUTOFF).astype(float)
                one = stats.describe_series(x[:n], np.random.default_rng(0), N_BOOT, distance=d[:n])
                two = stats.compare_series(x[:n], x[n:], np.random.default_rng(0), N_BOOT, d[:n], d[n:])
                if one["tau"] is None or two["n_blocks_a"] is None or two["n_blocks_b"] is None:
                    continue  # constant window: no CI to check
                rows.append({
                    "process": name,
                    "window": n,
                    "chunks": one["n_blocks"],
                    "chunks_min_pair": min(two["n_blocks_a"], two["n_blocks_b"]),
                    "freq_tau": one["ci_low"] <= p_true <= one["ci_high"],
                    "freq_boot": one["boot_ci_low"] <= p_true <= one["boot_ci_high"],
                    "diff_tau": two["ci_low"] <= 0.0 <= two["ci_high"],
                    "diff_boot": two["boot_ci_low"] <= 0.0 <= two["boot_ci_high"],
                })
        print(f"{name}: {time.perf_counter() - t0:.0f} s", flush=True)
    return rows


def _cov(hits: list[bool]) -> tuple[float, float]:
    p = float(np.mean(hits))
    return p, math.sqrt(p * (1 - p) / len(hits))


def by_process(rows: list[dict]) -> list[dict]:
    out = []
    for name in PROCESSES:
        for n in WINDOWS:
            sel = [r for r in rows if r["process"] == name and r["window"] == n]
            if not sel:
                continue
            out.append({
                "process": name, "window_frames": n, "replicates": len(sel),
                "median_chunks": round(float(np.median([r["chunks"] for r in sel])), 1),
                **{k: round(_cov([r[k] for r in sel])[0], 3) for k in ("freq_tau", "freq_boot", "diff_tau", "diff_boot")},
            })
    return out


def by_chunks(rows: list[dict]) -> list[dict]:
    out = []
    for lo, hi in zip(CHUNK_EDGES[:-1], CHUNK_EDGES[1:]):
        freq = [r for r in rows if lo <= r["chunks"] < hi]
        diff = [r for r in rows if lo <= r["chunks_min_pair"] < hi]
        if not freq and not diff:
            continue
        row = {"chunks_from": lo, "chunks_to": "inf" if math.isinf(hi) else hi,
               "n_freq": len(freq), "n_diff": len(diff)}
        for key, sel in (("freq_tau", freq), ("freq_boot", freq), ("diff_tau", diff), ("diff_boot", diff)):
            cov, se = _cov([r[key] for r in sel]) if sel else (float("nan"), float("nan"))
            row[key], row[f"{key}_se"] = round(cov, 3), round(se, 3)
        out.append(row)
    return out


def min_blocks_firm(table: list[dict]) -> float | None:
    """Smallest bin lower edge from which every bin has tau-based coverage >= TARGET (freq and diff)."""
    ok = [r["freq_tau"] >= TARGET and (r["n_diff"] == 0 or r["diff_tau"] >= TARGET) for r in table]
    for i, row in enumerate(table):
        if all(ok[i:]):
            return row["chunks_from"]
    return None


def write_csv(path: Path, rows: list[dict]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--reps", type=int, default=150, help="replicates per process and window length")
    ap.add_argument("--out", type=Path, default=REPO_ROOT / "notes" / "calibration")
    args = ap.parse_args()

    rows = run(args.reps)
    proc_table, chunk_table = by_process(rows), by_chunks(rows)
    args.out.mkdir(parents=True, exist_ok=True)
    write_csv(args.out / "coverage_by_process.csv", proc_table)
    write_csv(args.out / "coverage_by_chunks.csv", chunk_table)

    print("\nCoverage by independent chunks per window (tau-based | bootstrap cross-check):")
    for r in chunk_table:
        print(f"  {r['chunks_from']:>4}-{r['chunks_to']:<4} freq {r['freq_tau']:.3f} | {r['freq_boot']:.3f} (n={r['n_freq']})"
              f"   diff {r['diff_tau']:.3f} | {r['diff_boot']:.3f} (n={r['n_diff']})")
    print(f"\nMIN_BLOCKS_FIRM (coverage >= {TARGET:.0%} from here up): {min_blocks_firm(chunk_table)}")
    print(f"Tables written to {args.out}")


if __name__ == "__main__":
    main()
