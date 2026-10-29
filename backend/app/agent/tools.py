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
from typing import Any, Callable

import numpy as np

from app.config import dataset_config
from app.science import contact_stats as cs
from app.science.contacts import CONTACT_DEFINITION, calculate_contacts, validate_cutoff, validate_window
from app.science.distances import distance_over_time
from app.science.loader import TRAJ_LOCK, load_universe
from app.science.native_contacts import native_contacts
from app.science.residues import make_residue_key, parse_residue_key

TOP_N = 8  # rows per table in a model summary (keeps it well under ~2k tokens)


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
    steps: list[dict] = field(default_factory=list)  # [{tool, rationale}]

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


def resolve_pair(plan: ApprovedPlan, label: str) -> tuple[str, str]:
    """'HSE134-ASP118' -> (key in group_a, key in group_b). Both residues must be in the approved groups."""
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
    ra, rb = (parse_residue_key(k).resid for k in keys)
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
                               plan.sequence_exclusion)
    freq = np.where(r["excluded"], np.nan, r["frequency"])
    flat = [(float(freq[i, j]), r["keys_a"][i], r["keys_b"][j]) for i, j in zip(*np.nonzero(freq > 0))]
    flat.sort(key=lambda t: -t[0])
    values = {"n_pairs_in_contact": _v(len(flat), "count"), "n_frames": _v(r["n_frames"], "count")}
    top = []
    for f, ka, kb in flat[:TOP_N]:
        lab = pair_label(ka, kb)
        values[f"{lab}.freq"] = _v(f, "freq")
        top.append({"pair": lab, "freq": _r(f)})
    summary = {"window": [start, stop], "n_frames": r["n_frames"], "n_pairs_ever_in_contact": len(flat),
               "top_pairs_by_frequency": top}
    data = {**r, "frequency": freq, "window": {"start": start, "stop": stop, "stride": plan.stride}}
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
                                 plan.cutoff, plan.stride, plan.sequence_exclusion)
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
                                  plan.robustness_cutoffs, pairs, plan.stride, plan.sequence_exclusion)
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


def _native_contacts(plan, args):
    start, stop = _window(plan, args.get("window") or "all")
    selection = args.get("selection") or "name CA"
    radius = float(args.get("radius") or 8.0)
    ref = args.get("ref_frames") or [0, -1]
    with TRAJ_LOCK:
        r = native_contacts(plan.dataset, selection, radius, tuple(int(f) for f in ref), start, stop, plan.stride)
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


# ---------- schemas (Responses API flat function tools) ----------

def _tool(name: str, description: str, props: dict, required: list[str] | None = None) -> dict:
    props = {"dataset": {"type": "string", "description": "Optional; must equal the approved dataset."}, **props}
    return {"type": "function", "name": name, "description": description,
            "parameters": {"type": "object", "properties": props, "required": required or [],
                           "additionalProperties": False}}


WINDOW = {"type": "string", "enum": ["a", "b", "all"], "description": "Approved window: 'a', 'b', or 'all' (both)."}
PAIR = {"type": "string", "description": "Residue pair from the approved groups, e.g. 'HSE134-ASP118'."}

SCHEMAS = [
    _tool("get_metadata", "Dataset facts: frames, residues, time step, stats validity, groups.", {}),
    _tool("list_groups", "Named residue groups (domains) with their ranges and source.", {}),
    _tool("calculate_contacts", "Contact frequency of every residue pair between the two approved groups over one approved window, at the approved cutoff. Returns the most frequent pairs.", {"window": WINDOW}),
    _tool("distance_over_time", "Minimum heavy-atom distance per frame for one residue pair.", {"pair": PAIR, "window": WINDOW}, ["pair"]),
    _tool("compare_intervals", "Per-pair contact-frequency change, window b minus window a, with autocorrelation-corrected CIs and BH-FDR q-values; says which pairs are supported.", {}),
    _tool("contact_statistics", "For one pair in one window: frequency, autocorrelation time in ns, independent chunks, and its CI.", {"pair": PAIR, "window": WINDOW}, ["pair"]),
    _tool("cutoff_sensitivity", "Rerun the comparison at every approved robustness cutoff. A pair is robust only if supported at every cutoff with the same sign; otherwise cutoff-sensitive.", {"pairs": {"type": "array", "items": PAIR, "description": "Pairs to judge; default: every pair supported at any cutoff."}}),
    _tool("native_contacts", "Fraction of native contacts per frame against reference frames (MDAnalysis Q1/Q2 method).", {"window": WINDOW, "selection": {"type": "string"}, "radius": {"type": "number"}, "ref_frames": {"type": "array", "items": {"type": "integer"}}}),
    _tool("update_checklist", "Mark a plan item done, in progress or skipped (a skip needs a reason in note).", {"item": {"type": "integer", "description": "1-based plan item"}, "status": {"type": "string", "enum": ["in_progress", "done", "skipped"]}, "note": {"type": "string"}}, ["item", "status"]),
    _tool("record_finding", "Record one finding. Text uses placeholders like {{r2.HSE134-ASP118.delta}} for every number; never write numbers. Cite evidence ids (a result 'r2' or a pair 'r2.HSE134-ASP118').", {
        "text": {"type": "string"},
        "evidence_ids": {"type": "array", "items": {"type": "string"}},
        "claim": {"type": "string", "enum": ["supported", "not_supported", "descriptive"],
                  "description": "supported: a statistically supported change; not_supported: an apparent change the statistics do not support; descriptive: an observation with no support claim."}},
        ["text", "evidence_ids", "claim"]),
    _tool("submit_report", "Submit the report from recorded findings. Runs the full deterministic check; if it fails, fix the listed problems and resubmit.", {
        "finding_ids": {"type": "array", "items": {"type": "string"}},
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
    "native_contacts": _native_contacts,
}
STATS_TOOLS = {"compare_intervals", "contact_statistics", "cutoff_sensitivity"}
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
        data, summary, values, pairs = SCIENCE[name](state.plan, args)
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


def _store(state, name, args, status, summary, data=None, values=None, pairs=None) -> dict:
    pid = state.next_id("r", state.results)
    units = {"distance": "Å", "time": dataset_config(state.plan.dataset)["time_label"]}
    summary = {"provenance_id": pid, "status": status, **summary}
    if status == "ok" and values:
        summary["placeholders"] = f"{{{{{pid}.<name>}}}} for: " + ", ".join(sorted({k.split('.')[-1] for k in values}))
    result = {
        "status": status, "tool": name, "provenance_id": pid,
        "params": {**args, "dataset": state.plan.dataset, "cutoff": state.plan.cutoff},
        "units": units,
        "summary_for_model": json.dumps(jsonable(summary)),
        "data_for_ui": jsonable(data),
        "values": jsonable(values or {}),
        "pairs": jsonable(pairs or {}),
    }
    state.results[pid] = result
    return result
