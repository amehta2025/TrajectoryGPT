"""Data reconnaissance (SPEC §5d). Rerunnable.

Prints contact-frequency summaries for LID-NMP, LID-CORE and NMP-CORE and the
precompute timing. Run from backend/:  python -m scripts.recon [--dataset adk_equilibrium]
"""
import argparse
import sys
import time

import numpy as np

from app.science.contacts import calculate_contacts, precompute_min_distances
from app.science.loader import load_universe, pbc_mode
from app.science.residues import parse_residue_key

PAIRS = [("LID", "NMP"), ("LID", "CORE"), ("NMP", "CORE")]


def summarize(dataset: str, a: str, b: str, cutoff: float, top: int) -> None:
    t0 = time.perf_counter()
    md = precompute_min_distances(dataset, a, b, progress=print)
    elapsed = time.perf_counter() - t0
    res = calculate_contacts(dataset, a, b, cutoff=cutoff)
    freq = res["frequency"]
    series = md.min_dist <= cutoff  # [frame, i, j]
    transitions = np.abs(np.diff(series.astype(np.int8), axis=0)).sum(axis=0)
    local = res["excluded"]  # sequence-local pairs, |Δresid| <= sequence_exclusion

    ever = (freq > 0) & ~local
    variable = (freq >= 0.05) & (freq <= 0.95) & ~local
    print(f"\n### {a}-{b}  (cutoff {cutoff} Å, {res['n_frames']} frames)")
    print(f"- precompute: {md.min_dist.shape} in {elapsed:.1f} s "
          f"({'computed' if md.computed else 'loaded from cache'})")
    print(f"- pairs: {freq.size} total; {int(local.sum())} sequence-local "
          f"(|Δresid| <= {res['sequence_exclusion']}, excluded below)")
    print(f"- ever in contact: {int(ever.sum())}; variable (5-95%): {int(variable.sum())}; "
          f">=50%: {int(((freq >= 0.5) & ~local).sum())}")
    print(f"- median transitions per ever-contacting pair: "
          f"{float(np.median(transitions[ever])) if ever.any() else 0:.0f}")
    _table("Most frequent", np.where(ever, freq, -1.0), ever, md, freq, transitions, top)
    # Closest to 50% first: these pairs have the most room to change between windows.
    _table("Most variable (freq 5-95%)", np.where(variable, -np.abs(freq - 0.5), -np.inf),
           variable, md, freq, transitions, top)


def _table(title, score, mask, md, freq, transitions, top) -> None:
    print(f"\n{title}:\n\n| pair | freq | transitions | min dist (Å) |\n|---|---|---|---|")
    for flat in np.argsort(score, axis=None)[::-1][:top]:
        i, j = np.unravel_index(flat, freq.shape)
        if not mask[i, j]:
            break
        ka, kb = parse_residue_key(md.keys_a[i]), parse_residue_key(md.keys_b[j])
        print(f"| {ka.resname}{ka.resid}-{kb.resname}{kb.resid} | {freq[i, j]:.3f} | "
              f"{int(transitions[i, j])} | {md.min_dist[:, i, j].min():.2f} |")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--dataset", default="adk_equilibrium")
    p.add_argument("--cutoff", type=float, default=4.5)
    p.add_argument("--top", type=int, default=10)
    args = p.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    u = load_universe(args.dataset)
    print(f"## Recon: {args.dataset}  ({u.atoms.n_atoms} atoms, {u.trajectory.n_frames} frames, "
          f"pbc: {pbc_mode(args.dataset)})")
    for a, b in PAIRS:
        summarize(args.dataset, a, b, args.cutoff, args.top)


if __name__ == "__main__":
    main()
