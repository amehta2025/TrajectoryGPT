"""Registered agent tools (SPEC §7): schemas, guardrails, dispatch, short summaries, placeholder values.

The model can only call these. Every call is checked against the approved plan here, in code:
dataset, groups, cutoff, windows and the robustness cutoffs come from the plan, never from the model.
Each result gets a provenance id (r1, r2, ...) and a flat table of values that placeholders such as
{{r2.HSE134-ASP118.delta}} resolve to. Full data goes to the UI; the model gets a short summary.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, Callable

import MDAnalysis
import scipy

import numpy as np

from app.config import dataset_config
from app.science import contact_stats as cs
from app.science import stats
from app.science.contacts import (CONTACT_DEFINITION, calculate_contacts, contact_difference_map, dataset_hashes,
                                  validate_cutoff, validate_window)
from app.science.distances import distance_over_time
from app.science.domain_opening import REFERENCE as OPENING_REFERENCE, domain_opening
from app.science.flexibility import residue_flexibility
from app.science.loader import TRAJ_LOCK, load_universe
from app.science.native_contacts import PUBLISHED_DATASET, native_contacts, reproduction_check
from app.science.residues import make_residue_key, parse_residue_key

TOP_N = 8  # rows per table in a model summary (keeps it well under ~2k tokens)
# Atom selections native_contacts accepts (the agent cannot pass free-form MDAnalysis selections).
NATIVE_SELECTIONS = {"name CA": "name CA", "heavy atoms": "protein and not name H*"}


# ---------- approved plan and run state ----------

@dataclass
class ApprovedPlan:
    """The human-approved parameters (SPEC §4). The backend enforces them on every tool call."""
    dataset: str
    group_a: str
    group_b: str
    cutoff: float
    window_a: list[int]               # [start, stop) frame indices
    window_b: list[int] | None = None  # second window for comparisons, or None
    stride: int = 1
    sequence_exclusion: int = 2
    robustness_cutoffs: list[float] = field(default_factory=lambda: list(cs.DEFAULT_ROBUST_CUTOFFS))
    # Native contacts (figure mode): approved like the cutoff, so a user's edit is what runs.
    native_selection: str = "name CA"  # a key of NATIVE_SELECTIONS
    native_radius: float = 8.0         # Å
    ref_frames: list[int] = field(default_factory=lambda: [0, -1])
    # Residue ranges a figure's axes used (e.g. {"CORE": [100, 121]}): contacts, tests and the multiple-comparison
    # correction cover only these residues of that group, so the figure, the statistics and the report agree.
    residue_ranges: dict[str, list[int]] | None = None
    pair: str | None = None  # a residue pair the user chose (a figure from another protein); tools use exactly it
    steps: list[dict] = field(default_factory=list)  # [{tool, rationale}]
    notes: list[str] = field(default_factory=list)  # code-written data notes (agent/limits.py), shown as caveats

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class RunState:
    plan: ApprovedPlan
    results: dict[str, dict] = field(default_factory=dict)   # provenance id -> stored result
    findings: dict[str, dict] = field(default_factory=dict)  # f1, f2, ...
    checklist: list[dict] = field(default_factory=list)
    report: dict | None = None
    extra_steps: list[str] = field(default_factory=list)

    def __post_init__(self):
        if not self.checklist:
            self.checklist = [{"item": i + 1, "tool": s["tool"], "text": s.get("rationale", ""), "status": "pending", "note": ""}
                              for i, s in enumerate(self.plan.steps)]

    def next_id(self, prefix: str, table: dict) -> str:
        return f"{prefix}{len(table) + 1}"


class Refused(Exception):
    """A guardrail or validation refusal; str(e) goes back to the model."""


# ---------- helpers ----------

def jsonable(x: Any) -> Any:
    if isinstance(x, dict):
        return {str(k): jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [jsonable(v) for v in x]
    if isinstance(x, np.ndarray):
        return jsonable(x.tolist())
    if isinstance(x, (np.bool_,)):
        return bool(x)
    if isinstance(x, np.integer):
        return int(x)
    if isinstance(x, (float, np.floating)):
        x = float(x)
        return None if np.isnan(x) else x
    return x


def res_label(key: str) -> str:
    k = parse_residue_key(key)
    return f"{k.resname}{k.resid}"


def pair_label(key_a: str, key_b: str) -> str:
    return f"{res_label(key_a)}-{res_label(key_b)}"


def _in_group(dataset: str, group: str, resid: int) -> bool:
    return any(a <= resid <= b for a, b in dataset_config(dataset)["domains"][group])


def same_pair(a: str | None, b: str | None) -> bool:
    """'asp54-lys157' and 'LYS157 - ASP54' name the same pair."""
    norm = lambda s: sorted(x.strip().upper() for x in (s or "").split("-"))
    return bool(a and b) and norm(a) == norm(b)


def resolve_pair(plan: ApprovedPlan, label: str | None) -> tuple[str, str]:
    """'HSE134-ASP118' -> (key in group_a, key in group_b). Both residues must be in the approved groups, unless
    the plan carries a pair the user chose: then only that pair is allowed, wherever its residues are."""
    if plan.pair:
        if label and not same_pair(label, plan.pair):
            raise Refused(f"the approved residue pair is {plan.pair} (chosen by the user); call without a pair or with it")
        label = plan.pair
    m = re.fullmatch(r"\s*([A-Za-z]{3})(\d+)\s*-\s*([A-Za-z]{3})(\d+)\s*", label or "")
    if not m:
        raise Refused(f"pair must look like 'HSE134-ASP118', got {label!r}")
    u = load_universe(plan.dataset)
    keys = []
    for resname, resid in ((m[1].upper(), int(m[2])), (m[3].upper(), int(m[4]))):
        res = [r for r in u.residues if r.resid == resid]
        if not res or res[0].resname != resname:
            actual = res[0].resname if res else "none"
            raise Refused(f"residue {resname}{resid} not found (residue {resid} is {actual})")
        keys.append(make_residue_key(plan.dataset, res[0].segid, resid, resname))
    if plan.pair:
        return keys[0], keys[1]
    ra, rb = (parse_residue_key(k).resid for k in keys)
    if plan.residue_ranges:  # a pair outside the approved ranges was never tested; it cannot be analysed either
        rng = plan.residue_ranges
        inside = lambda g, r: g not in rng or rng[g][0] <= r <= rng[g][1]
        if not ((inside(plan.group_a, ra) and inside(plan.group_b, rb)) or (inside(plan.group_a, rb) and inside(plan.group_b, ra))):
            raise Refused(f"{label} is outside the approved residue ranges "
                          + ", ".join(f"{g} {a}-{b}" for g, (a, b) in rng.items()))
    if _in_group(plan.dataset, plan.group_b, ra) and _in_group(plan.dataset, plan.group_a, rb):
        keys.reverse()
        ra, rb = rb, ra
    if not (_in_group(plan.dataset, plan.group_a, ra) and _in_group(plan.dataset, plan.group_b, rb)):
        raise Refused(f"{label} is not a {plan.group_a}-{plan.group_b} pair; only the approved groups may be analysed")
    return keys[0], keys[1]


def _window(plan: ApprovedPlan, which: str | None) -> tuple[int, int | None]:
    which = which or "a"
    if which == "a":
        return plan.window_a[0], plan.window_a[1]
    if which == "b":
        if plan.window_b is None:
            raise Refused("this plan has only one window ('a')")
        return plan.window_b[0], plan.window_b[1]
    if which == "all":
        ws = [plan.window_a] + ([plan.window_b] if plan.window_b else [])
        return min(w[0] for w in ws), max(w[1] for w in ws)
    raise Refused(f"window must be 'a', 'b' or 'all', got {which!r}")


def _r(x, nd=3):
    return None if x is None else round(float(x), nd)


def _v(value, kind: str) -> dict:
    return {"v": jsonable(value), "kind": kind}


# ---------- science tool implementations: (plan, args) -> (full data, summary, values, pairs) ----------

def _get_metadata(plan, args):
    cfg = dataset_config(plan.dataset)
    with TRAJ_LOCK:
        u = load_universe(plan.dataset)
        n_frames, n_res, n_atoms = u.trajectory.n_frames, u.residues.n_residues, u.atoms.n_atoms
    data = {"dataset": plan.dataset, "n_frames": n_frames, "n_residues": n_res, "n_atoms": n_atoms,
            "dt_ns": cfg["dt_ns"], "time_label": cfg["time_label"], "stats_valid": cfg["stats_valid"],
            "groups": cfg["domains"], "description": cfg["description"].strip()}
    values = {"n_frames": _v(n_frames, "count"), "n_residues": _v(n_res, "count")}
    return data, data, values, {}


def _list_groups(plan, args):
    cfg = dataset_config(plan.dataset)
    data = {"groups": cfg["domains"], "source": cfg["domains_source"]["citation"].strip()}
    return data, data, {}, {}


def _calculate_contacts(plan, args):
    start, stop = _window(plan, args.get("window"))
    with TRAJ_LOCK:
        r = calculate_contacts(plan.dataset, plan.group_a, plan.group_b, plan.cutoff, start, stop, plan.stride,
                               plan.sequence_exclusion, ranges=plan.residue_ranges)
    freq = np.where(r["excluded"], np.nan, r["frequency"])
    flat = [(float(freq[i, j]), r["keys_a"][i], r["keys_b"][j]) for i, j in zip(*np.nonzero(freq > 0))]
    flat.sort(key=lambda t: -t[0])
    values = {"n_pairs_in_contact": _v(len(flat), "count"), "n_frames": _v(r["n_frames"], "count"),
              "any_contact_freq": _v(r["any_contact_frequency"], "freq")}
    top, pairs = [], {}
    for f, ka, kb in flat[:TOP_N]:
        lab = pair_label(ka, kb)
        values[f"{lab}.freq"] = _v(f, "freq")
        top.append({"pair": lab, "freq": _r(f)})
        pairs[lab] = {"key_a": ka, "key_b": kb}  # so a finding can cite a listed pair, e.g. 'r1.ASP54-LYS157'
    summary = {"window": [start, stop], "n_frames": r["n_frames"], "n_pairs_ever_in_contact": len(flat),
               "fraction_of_frames_groups_touch": _r(r["any_contact_frequency"]),
               "top_pairs_by_frequency": top}
    data = {**r, "frequency": freq, "window": {"start": start, "stop": stop, "stride": plan.stride}}
    return data, summary, values, pairs


def _contact_difference_map(plan, args, state):
    """Window B minus window A per pair. Supported flags come from a compare_intervals result already in
    this run with the same windows and cutoff; without one (or on stats_valid: false) the map is raw."""
    if plan.window_b is None:
        raise Refused("contact_difference_map compares two windows; this plan has one")
    with TRAJ_LOCK:
        r = contact_difference_map(plan.dataset, plan.group_a, plan.group_b, tuple(plan.window_a), tuple(plan.window_b),
                                   plan.cutoff, plan.stride, plan.sequence_exclusion, ranges=plan.residue_ranges)
    supported: dict[tuple[str, str], bool] | None = None
    stats_valid = dataset_config(plan.dataset)["stats_valid"]
    if stats_valid:
        for res in state.results.values():
            ui = res.get("data_for_ui") or {}
            if (res["tool"] == "compare_intervals" and res["status"] == "ok" and ui.get("window_a") == list(plan.window_a)
                    and ui.get("window_b") == list(plan.window_b) and ui.get("cutoff") == plan.cutoff):
                supported = {(row["key_a"], row["key_b"]): bool(row["supported"]) for row in ui["rows"]}
    delta = np.where(r["excluded"], np.nan, r["delta"])
    flat = [(float(delta[i, j]), i, j) for i, j in zip(*np.nonzero(np.nan_to_num(delta) != 0))]
    values, pairs, top = {}, {}, {"lost": [], "gained": []}
    for side, rows in (("lost", sorted(flat)[:TOP_N]), ("gained", sorted(flat, reverse=True)[:TOP_N])):
        for d, i, j in rows:
            if (side == "lost") != (d < 0):
                continue
            ka, kb = r["keys_a"][i], r["keys_b"][j]
            lab = pair_label(ka, kb)
            sup = None if supported is None else supported.get((ka, kb), False)
            values[f"{lab}.delta"] = _v(d, "delta")
            values[f"{lab}.freq_a"] = _v(r["freq_a"][i, j], "freq")
            values[f"{lab}.freq_b"] = _v(r["freq_b"][i, j], "freq")
            # Not "supported": a supported claim must cite the compare_intervals result itself (CI, q), not this map.
            pairs[lab] = {"key_a": ka, "key_b": kb, "delta": d, **({} if sup is None else {"supported_in_compare": sup})}
            top[side].append({"pair": lab, "delta": _r(d), "freq_a": _r(r["freq_a"][i, j]), "freq_b": _r(r["freq_b"][i, j]),
                              **({} if sup is None else {"supported_in_compare": sup})})
    tested = ("statistics joined from compare_intervals (same windows and cutoff)" if supported is not None else
              "not statistically tested" if not stats_valid else
              "raw differences; run compare_intervals for which changes are supported")
    summary = {"windows": [plan.window_a, plan.window_b], "n_pairs_changed": len(flat), "statistics": tested,
               "most_lost": top["lost"], "most_gained": top["gained"]}
    data = {**r, "delta": delta, "tested": supported is not None,
            "supported_pairs": None if supported is None else [list(k) for k, v in supported.items() if v]}
    return data, summary, values, pairs


def _residue_flexibility(plan, args):
    start, stop = _window(plan, args.get("window") or "all")
    with TRAJ_LOCK:
        r = residue_flexibility(plan.dataset, start, stop if stop is not None else None, plan.stride)
    rmsf, keys = r["rmsf"], r["keys"]
    order = np.argsort(-rmsf)[:TOP_N]
    values = {f"{res_label(keys[i])}.rmsf": _v(rmsf[i], "distance") for i in order}
    per_domain = {}
    for name, ranges in dataset_config(plan.dataset)["domains"].items():
        mask = np.zeros(len(keys), bool)
        for a, b in ranges:
            mask |= (r["resids"] >= a) & (r["resids"] <= b)
        per_domain[name] = _r(np.median(rmsf[mask]), 2)
        values[f"median_rmsf.{name}"] = _v(np.median(rmsf[mask]), "distance")
    summary = {"window": [start, stop], "n_frames": r["n_frames"], "fit": "CORE C-alphas", "note": r["note"],
               "median_rmsf_by_domain_A": per_domain,
               "most_flexible": [{"residue": res_label(keys[i]), "rmsf_A": _r(rmsf[i], 2)} for i in order]}
    data = {**r, "window": {"start": start, "stop": stop, "stride": plan.stride}}
    return data, summary, values, {}


def _domain_opening(plan, args):
    """COM distance of an approved mobile group to CORE per frame; descriptive (no statistical test)."""
    mobile = [g for g in (plan.group_a, plan.group_b) if g != OPENING_REFERENCE]
    domain = args.get("domain") or (mobile[0] if mobile else None)
    if domain not in mobile:
        raise Refused(f"domain must be one of the approved groups other than {OPENING_REFERENCE}: {mobile}")
    start, stop = _window(plan, args.get("window") or "all")
    with TRAJ_LOCK:
        r = domain_opening(plan.dataset, domain, start, stop, plan.stride)
    d, frames = r["distance"], r["frames"]
    i_min, i_max = int(np.argmin(d)), int(np.argmax(d))
    name = f"{domain}-{OPENING_REFERENCE}"
    values = {f"{name}.min_distance": _v(d.min(), "distance"), f"{name}.median_distance": _v(np.median(d), "distance"),
              f"{name}.max_distance": _v(d.max(), "distance"), f"{name}.first_distance": _v(d[0], "distance"),
              f"{name}.last_distance": _v(d[-1], "distance"),
              f"{name}.frame_of_min": _v(int(frames[i_min]), "frame"), f"{name}.frame_of_max": _v(int(frames[i_max]), "frame")}
    summary = {"domain": domain, "reference": OPENING_REFERENCE, "window": [start, stop], "n_frames": r["n_frames"],
               "first_A": _r(d[0], 2), "last_A": _r(d[-1], 2), "min_A": _r(d.min(), 2), "median_A": _r(np.median(d), 2),
               "max_A": _r(d.max(), 2), "frame_of_min": int(frames[i_min]), "frame_of_max": int(frames[i_max]),
               "note": r["note"] + " Descriptive only: not statistically tested."}
    data = {**r, "window": {"start": start, "stop": stop, "stride": plan.stride}}
    return data, summary, values, {}


def _distance_over_time(plan, args):
    ka, kb = resolve_pair(plan, args.get("pair"))
    start, stop = _window(plan, args.get("window") or "all")
    with TRAJ_LOCK:
        r = distance_over_time(plan.dataset, ka, kb, start, stop, plan.stride)
    d = r["distance"]
    lab = pair_label(ka, kb)
    i_min = int(np.argmin(d))
    frac = float(np.mean(d <= plan.cutoff))
    values = {f"{lab}.min_distance": _v(d.min(), "distance"), f"{lab}.median_distance": _v(np.median(d), "distance"),
              f"{lab}.max_distance": _v(d.max(), "distance"), f"{lab}.frac_in_contact": _v(frac, "freq"),
              f"{lab}.frame_of_min": _v(int(r["frames"][i_min]), "frame")}
    summary = {"pair": lab, "window": [start, stop], "n_frames": len(d), "min_A": _r(d.min(), 2),
               "median_A": _r(np.median(d), 2), "max_A": _r(d.max(), 2), "frac_frames_within_cutoff": _r(frac),
               "frame_of_min": int(r["frames"][i_min])}
    return r, summary, values, {lab: {"key_a": ka, "key_b": kb}}


def _compare_intervals(plan, args):
    if plan.window_b is None:
        raise Refused("compare_intervals needs two approved windows; this plan has one")
    with TRAJ_LOCK:
        r = cs.compare_intervals(plan.dataset, plan.group_a, plan.group_b, tuple(plan.window_a), tuple(plan.window_b),
                                 plan.cutoff, plan.stride, plan.sequence_exclusion, ranges=plan.residue_ranges)
    values = {k: _v(r[k], "count") for k in ("n_tested", "n_filtered", "n_supported", "n_supported_firm",
                                             "n_supported_provisional")}
    pairs, top = {}, []
    for row in r["rows"]:
        lab = pair_label(row["key_a"], row["key_b"])
        pairs[lab] = {"key_a": row["key_a"], "key_b": row["key_b"], "supported": row["supported"],
                      "provisional": row["provisional"], "delta": row["delta"]}
        for k, kind in (("freq_a", "freq"), ("freq_b", "freq"), ("delta", "delta"), ("ci_low", "delta"),
                        ("ci_high", "delta"), ("q", "q"), ("n_blocks_a", "chunks"), ("n_blocks_b", "chunks"),
                        ("tau_ns_a", "tau_ns"), ("tau_ns_b", "tau_ns")):
            values[f"{lab}.{k}"] = _v(row[k], kind)
        values[f"{lab}.ci"] = _v([row["ci_low"], row["ci_high"]], "ci")
    for row in r["rows"][:TOP_N]:
        top.append({"pair": pair_label(row["key_a"], row["key_b"]), "freq_a": _r(row["freq_a"]), "freq_b": _r(row["freq_b"]),
                    "delta": _r(row["delta"]), "ci": [_r(row["ci_low"]), _r(row["ci_high"])], "q": _r(row["q"], 4),
                    "chunks": [_r(row["n_blocks_a"], 0), _r(row["n_blocks_b"], 0)],
                    "status": ("supported (slow - provisional)" if row["provisional"] else "supported") if row["supported"]
                    else ("not supported (provisional)" if row["provisional"] else "not supported")})
    summary = {"windows": [plan.window_a, plan.window_b], "cutoff_A": plan.cutoff, "n_tested": r["n_tested"],
               "n_filtered_rare": r["n_filtered"], "n_supported": r["n_supported"],
               "n_supported_firm": r["n_supported_firm"], "n_supported_provisional": r["n_supported_provisional"],
               "fdr": r["fdr"], "rows (supported first, then by q)": top}
    return r, summary, values, pairs


def _contact_statistics(plan, args):
    ka, kb = resolve_pair(plan, args.get("pair"))
    start, stop = _window(plan, args.get("window"))
    with TRAJ_LOCK:
        r = cs.contact_statistics(plan.dataset, ka, kb, plan.cutoff, start, stop, plan.stride)
    lab = pair_label(ka, kb)
    values = {f"{lab}.freq": _v(r["frequency"], "freq"), f"{lab}.ci_low": _v(r["ci_low"], "freq"),
              f"{lab}.ci_high": _v(r["ci_high"], "freq"), f"{lab}.ci": _v([r["ci_low"], r["ci_high"]], "ci_freq"),
              f"{lab}.tau_ns": _v(r["tau_ns"], "tau_ns"), f"{lab}.n_eff": _v(r["n_eff"], "chunks"),
              f"{lab}.transitions": _v(r["transitions"], "count")}
    summary = {"pair": lab, "window": [start, stop], "freq": _r(r["frequency"]), "ci": [_r(r["ci_low"]), _r(r["ci_high"])],
               "tau_ns": _r(r["tau_ns"], 2), "independent_chunks": _r(r["n_eff"], 0), "transitions": r["transitions"],
               "provisional": r["provisional"], "note": r["stats_note"]}
    return r, summary, values, {lab: {"key_a": ka, "key_b": kb, "provisional": r["provisional"]}}


def _cutoff_sensitivity(plan, args):
    if plan.window_b is None:
        raise Refused("cutoff_sensitivity compares two windows; this plan has one")
    pairs = None
    if args.get("pairs"):
        pairs = [resolve_pair(plan, p) for p in args["pairs"]]
    with TRAJ_LOCK:
        r = cs.cutoff_sensitivity(plan.dataset, plan.group_a, plan.group_b, tuple(plan.window_a), tuple(plan.window_b),
                                  plan.robustness_cutoffs, pairs, plan.stride, plan.sequence_exclusion,
                                  ranges=plan.residue_ranges)
    cuts = r["cutoffs"]
    values = {"n_robust": _v(r["n_robust"], "count"), "range": _v([cuts[0], cuts[-1]], "cutoff_range")}
    out_pairs, rows = {}, []
    for row in r["rows"]:
        lab = pair_label(row["key_a"], row["key_b"])
        out_pairs[lab] = {"key_a": row["key_a"], "key_b": row["key_b"], "verdict": row["verdict"],
                          "holds_at": row["holds_at"], "cutoffs": cuts}
        values[f"{lab}.holds_at"] = _v(row["holds_at"], "cutoff_list")
        for p in row["per_cutoff"]:
            tag = f"{p['cutoff']:.1f}".replace(".", "p")
            if p["tested"]:
                values[f"{lab}.delta_{tag}"] = _v(p["delta"], "delta")
                values[f"{lab}.q_{tag}"] = _v(p["q"], "q")
        rows.append({"pair": lab, "verdict": row["verdict"], "holds_at_A": row["holds_at"],
                     "per_cutoff": [{"cutoff": p["cutoff"], "supported": p["supported"],
                                     **({"delta": _r(p["delta"]), "q": _r(p["q"], 4)} if p["tested"] else {"tested": False})}
                                    for p in row["per_cutoff"]]})
    summary = {"cutoffs_A": cuts, "n_robust": r["n_robust"], "rows": rows[:TOP_N]}
    return r, summary, values, out_pairs


def _native_settings(plan, args) -> tuple[str, float, tuple[int, ...]]:
    """Selection, radius and reference frames come from the approved plan (the user may have edited them);
    a call that asks for different ones is refused, like a call that changes the cutoff."""
    asked = {"selection": plan.native_selection, "radius": plan.native_radius, "ref_frames": list(plan.ref_frames)}
    for k, approved in asked.items():
        v = args.get(k)
        if v is not None and v != approved and not (k == "radius" and isinstance(v, (int, float)) and float(v) == approved):
            raise Refused(f"{k} is fixed by the approved plan ({approved}); call without it")
    if plan.native_selection not in NATIVE_SELECTIONS:
        raise Refused(f"selection must be one of {sorted(NATIVE_SELECTIONS)}")
    return NATIVE_SELECTIONS[plan.native_selection], float(plan.native_radius), tuple(int(f) for f in plan.ref_frames)


def _native_contacts(plan, args):
    start, stop = _window(plan, args.get("window") or "all")
    selection, radius, ref = _native_settings(plan, args)
    with TRAJ_LOCK:
        r = native_contacts(plan.dataset, selection, radius, ref, start, stop, plan.stride)
    q = r["q"]
    values, per_ref = {}, []
    for k, f in enumerate(r["ref_frames"]):
        values[f"ref{k}.q_first"] = _v(q[0, k], "freq")
        values[f"ref{k}.q_last"] = _v(q[-1, k], "freq")
        values[f"ref{k}.n_native"] = _v(r["n_native"][k], "count")
        per_ref.append({"ref_frame": f, "n_native": r["n_native"][k], "q_first": _r(q[0, k]), "q_last": _r(q[-1, k]),
                        "q_min": _r(q[:, k].min()), "q_max": _r(q[:, k].max())})
    summary = {"selection": selection, "radius_A": radius, "n_frames": len(r["frames"]), "per_reference": per_ref}
    return r, summary, values, {}


def _reproduction_check(plan, args):
    if plan.dataset != PUBLISHED_DATASET:  # e.g. the agent adding it to a method transfer on adk_equilibrium
        raise Refused(f"the published Q1/Q2 figure was made from {PUBLISHED_DATASET}; there is nothing to reproduce on "
                      f"{plan.dataset}. For a method transfer, say the numbers are not expected to match.")
    selection, radius, ref = _native_settings(plan, args)
    with TRAJ_LOCK:
        r = reproduction_check(plan.dataset, selection, radius, ref)
    e = r["endpoints"]
    values = {"max_abs_diff": _v(r["max_abs_diff"], "sci"), "tolerance": _v(r["tolerance"], "sci"),
              "n_values": _v(r["n_values"], "count"), "match": _v(r["match"], "match"),
              **{k: _v(v, "freq") for k, v in e.items()}}
    summary = {"reference": r["reference_method"], "match": r["match"], "max_abs_diff": r["max_abs_diff"],
               "tolerance": r["tolerance"], "n_values": r["n_values"], "endpoints": {k: _r(v) for k, v in e.items()},
               "changed_from_published": r["changed"]}
    return r, summary, values, {}


# ---------- schemas (Responses API flat function tools) ----------

def _tool(name: str, description: str, props: dict, required: list[str] | None = None) -> dict:
    props = {"dataset": {"type": "string", "description": "Optional; must equal the approved dataset."}, **props}
    return {"type": "function", "name": name, "description": description,
            "parameters": {"type": "object", "properties": props, "required": required or [],
                           "additionalProperties": False}}


WINDOW = {"type": "string", "enum": ["a", "b", "all"], "description": "Approved window: 'a', 'b', or 'all' (both)."}
PAIR = {"type": "string", "description": "Residue pair from the approved groups, e.g. 'HSE134-ASP118'. If the approved plan names a pair, omit this (that pair is used)."}

SCHEMAS = [
    _tool("get_metadata", "Dataset facts: frames, residues, time step, stats validity, groups.", {}),
    _tool("list_groups", "Named residue groups (domains) with their ranges and source.", {}),
    _tool("calculate_contacts", "Contact frequency of every residue pair between the two approved groups over one approved window ('all' = the whole run), at the approved cutoff. Returns the most frequent pairs and the fraction of frames in which the two groups touch at all. Use it for 'do these parts touch / how often'.", {"window": WINDOW}),
    _tool("distance_over_time", "Minimum heavy-atom distance per frame for one residue pair.", {"pair": PAIR, "window": WINDOW}, ["pair"]),
    _tool("compare_intervals", "Per-pair contact-frequency change, window b minus window a, with autocorrelation-corrected CIs and BH-FDR q-values; says which pairs are supported.", {}),
    _tool("contact_statistics", "For one pair in one window: frequency, autocorrelation time in ns, independent chunks, and its CI.", {"pair": PAIR, "window": WINDOW}, ["pair"]),
    _tool("cutoff_sensitivity", "Rerun the comparison at every approved robustness cutoff. A pair is robust only if supported at every cutoff with the same sign; otherwise cutoff-sensitive.", {"pairs": {"type": "array", "items": PAIR, "description": "Pairs to judge; default: every pair supported at any cutoff."}}),
    _tool("contact_difference_map", "Contact-frequency change per residue pair, window B minus window A, for the approved groups and cutoff. Raw differences; pairs are marked supported only if compare_intervals already ran on the same windows. Not statistically tested on a stats-invalid dataset.", {}),
    _tool("residue_flexibility", "Per-residue C-alpha RMSF over an approved window, after superposing CORE C-alphas. On a biased path this is a path RMSF, not equilibrium flexibility.", {"window": WINDOW}),
    _tool("domain_opening", "How open a mobile domain (LID or NMP) is in each frame: the centre-of-mass distance between it and CORE, in Å. Descriptive, not statistically tested. The domain must be one of the approved groups.", {"domain": {"type": "string", "description": "LID or NMP; must be an approved group. Default: the approved group that is not CORE."}, "window": WINDOW}),
    _tool("native_contacts", "Fraction of native contacts per frame against reference frames (MDAnalysis Q1/Q2 method). Atom selection, radius and reference frames are fixed by the approved plan.", {"window": WINDOW}),
    _tool("reproduction_check", "Exact-reproduction check: compares our native contacts (with the approved selection, radius and reference frames) with the published MDAnalysis User Guide code at the published settings (contacts.q1q2, name CA, 8 Å); reports the largest difference, whether it is within tolerance, and which approved settings differ from the published ones.", {}),
    _tool("update_checklist", "Mark a plan item done, in progress or skipped (a skip needs a reason in note).", {"item": {"type": "integer", "description": "1-based plan item"}, "status": {"type": "string", "enum": ["in_progress", "done", "skipped"]}, "note": {"type": "string"}}, ["item", "status"]),
    _tool("record_finding", "Record one finding. Text uses placeholders like {{r2.HSE134-ASP118.delta}} for every number; never write numbers. Cite evidence ids (a result 'r2' or a pair 'r2.HSE134-ASP118').", {
        "text": {"type": "string"},
        "evidence_ids": {"type": "array", "items": {"type": "string"}},
        "claim": {"type": "string", "enum": ["supported", "not_supported", "null", "descriptive"],
                  "description": "supported: a statistically supported change; not_supported: an apparent change the statistics do not support; "
                                 "null: no supported change at all, citing the compare_intervals result that shows it; "
                                 "descriptive: what a non-statistical result (contacts, distances, native contacts, metadata) shows, not statistically tested."},
        "plain": {"type": "string", "description": "The same finding in one plain sentence for a non-specialist: no jargon or statistics terms, "
                                                     "placeholders for every number ({{r1.ASP54-LYS157.freq_a|pct}} writes 53%)."}},
        ["text", "evidence_ids", "claim"]),
    _tool("submit_report", "Submit the report from recorded findings (at least one). Runs the full deterministic check; if it fails, fix the listed problems and resubmit.", {
        "finding_ids": {"type": "array", "items": {"type": "string"}},
        "headline": {"type": "string", "description": "One plain sentence that answers the question directly, with placeholders for every "
                                                        "number, e.g. 'Rarely: NMP and LID touch in {{r1.any_contact_freq|pct}} of frames.'"},
        "interpretation": {"type": "string", "description": "Optional: one hedged plain sentence on what the answer might mean, shown "
                                                              "labelled 'interpretation — not computed'. No numbers, no claims of support."},
        "next_questions": {"type": "array", "items": {"type": "string"},
                           "description": "One or two short follow-up questions a researcher might ask next, based on these findings "
                                          "(e.g. 'Does the HSE134-ASP118 change persist across the whole run?'). Shown as buttons; each "
                                          "goes through plan approval. No numbers except residue numbers and approved cutoffs."},
        "background": {"type": "array", "items": {"type": "string"},
                       "description": "For general questions only: brief textbook context, shown as 'background — not computed'. No numbers, no claims about this run."},
        "hypotheses": {"type": "array", "items": {"type": "string"}, "description": "Possible explanations, hedged."},
        "caveats": {"type": "array", "items": {"type": "string"}},
        "gaps": {"type": "array", "items": {"type": "string"}, "description": "What could not be established."},
        "follow_ups": {"type": "array", "items": {"type": "object", "properties": {
            "tool": {"type": "string"}, "pair": {"type": "string"}, "rationale": {"type": "string"}},
            "required": ["tool", "rationale"], "additionalProperties": False}}},
        ["finding_ids"]),
]

SCIENCE: dict[str, Callable] = {
    "get_metadata": _get_metadata, "list_groups": _list_groups, "calculate_contacts": _calculate_contacts,
    "distance_over_time": _distance_over_time, "compare_intervals": _compare_intervals,
    "contact_statistics": _contact_statistics, "cutoff_sensitivity": _cutoff_sensitivity,
    "native_contacts": _native_contacts, "contact_difference_map": _contact_difference_map,
    "residue_flexibility": _residue_flexibility, "reproduction_check": _reproduction_check,
    "domain_opening": _domain_opening,
}
STATS_TOOLS = {"compare_intervals", "contact_statistics", "cutoff_sensitivity"}
NEEDS_STATE = {"contact_difference_map"}  # reads earlier results in the run (compare_intervals) to mark supported pairs
FREE_TOOLS = {"get_metadata", "list_groups"}  # informational; never count as an extra step


def tools_for(dataset: str) -> list[dict]:
    """Tool schemas the model sees; stats tools are omitted on stats_valid: false datasets."""
    hide = set() if dataset_config(dataset)["stats_valid"] else STATS_TOOLS
    return [t for t in SCHEMAS if t["name"] not in hide]


# ---------- dispatch ----------

def _guard(state: RunState, name: str, args: dict) -> None:
    plan = state.plan
    if "dataset" in args and args["dataset"] not in (None, "", plan.dataset):
        raise Refused(f"dataset {args['dataset']!r} is not the approved dataset {plan.dataset!r}")
    for k in ("group_a", "group_b", "cutoff", "start", "stop", "stride", "cutoffs"):
        if k in args:
            raise Refused(f"'{k}' is fixed by the approved plan and cannot be set by a tool call")
    if name in SCIENCE and name not in FREE_TOOLS:
        planned = {s["tool"] for s in plan.steps}
        if name not in planned:
            if state.extra_steps and name not in state.extra_steps:
                raise Refused(f"only one extra step beyond the plan is allowed (already added {state.extra_steps[0]})")
            if name not in state.extra_steps:
                state.extra_steps.append(name)


def run_tool(state: RunState, name: str, raw_args: str | dict) -> dict:
    """Run one tool call. Always returns {status, summary_for_model, data_for_ui, params, units, provenance_id}."""
    from app.agent import report  # bookkeeping tools live with the deterministic checks

    try:
        args = raw_args if isinstance(raw_args, dict) else json.loads(raw_args or "{}")
        if not isinstance(args, dict):
            raise ValueError("arguments must be a JSON object")
    except ValueError as e:
        return _store(state, name, {}, "error", {"error": f"arguments are not valid JSON: {e}"})

    try:
        _guard(state, name, args)
        if name == "update_checklist":
            return _bookkeeping(state, name, args, report.update_checklist(state, args))
        if name == "record_finding":
            return _bookkeeping(state, name, args, report.record_finding(state, args))
        if name == "submit_report":
            return _bookkeeping(state, name, args, report.submit_report(state, args))
        if name not in SCIENCE:
            raise Refused(f"unknown tool {name!r}")
        if name in STATS_TOOLS and not dataset_config(state.plan.dataset)["stats_valid"]:
            raise cs.StatsRefused(dataset_config(state.plan.dataset)["stats_refusal"].strip())
        handler = SCIENCE[name]
        data, summary, values, pairs = handler(state.plan, args, state) if name in NEEDS_STATE else handler(state.plan, args)
    except (Refused, cs.StatsRefused) as e:
        return _store(state, name, args, "refused", {"refused": str(e)})
    except ValueError as e:
        return _store(state, name, args, "error", {"error": str(e)})
    return _store(state, name, args, "ok", summary, data, values, pairs)


def _bookkeeping(state, name, args, outcome: dict) -> dict:
    """outcome = {status, message, ...}; bookkeeping results are not evidence and get no provenance id."""
    summary = {k: v for k, v in outcome.items() if k != "data"}
    return {"status": outcome["status"], "tool": name, "summary_for_model": json.dumps(jsonable(summary)),
            "data_for_ui": jsonable(outcome.get("data")), "params": args, "units": None, "provenance_id": None}


@lru_cache(maxsize=1)
def library_versions() -> dict:
    return {"MDAnalysis": MDAnalysis.__version__, "numpy": np.__version__, "scipy": scipy.__version__}


def provenance_record(dataset: str) -> dict:
    """What export and reproduction need (SPEC §11); the orchestrator adds the model and its usage."""
    return {"dataset": dataset, "dataset_hashes": dataset_hashes(dataset), "seed": stats.SEED,
            "library_versions": library_versions(),
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def _store(state, name, args, status, summary, data=None, values=None, pairs=None) -> dict:
    pid = state.next_id("r", state.results)
    units = {"distance": "Å", "time": dataset_config(state.plan.dataset)["time_label"]}
    summary = {"provenance_id": pid, "status": status, **summary}
    if status == "ok" and values:
        # Exact names (a pair's values carry the pair: r2.ASP54-LYS157.freq), so the model never guesses a prefix.
        names = sorted(values)
        summary["placeholders"] = ", ".join(f"{{{{{pid}.{k}}}}}" for k in names[:24]) + (" ..." if len(names) > 24 else "")
    result = {
        "status": status, "tool": name, "provenance_id": pid,
        "params": {**args, **{k: v for k, v in state.plan.as_dict().items() if k != "steps"}},
        "units": units,
        "summary_for_model": json.dumps(jsonable(summary)),
        "data_for_ui": jsonable(data),
        "values": jsonable(values or {}),
        "pairs": jsonable(pairs or {}),
        "provenance": provenance_record(state.plan.dataset),
    }
    state.results[pid] = result
    return result
