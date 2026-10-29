"""Planner: question -> schema-enforced plan -> code validation (SPEC §9 steps 2-3).

An invalid plan goes back to the model once with the errors; a second failure is raised for the user.
"""
from __future__ import annotations

from app.agent import prompts
from app.agent.llm import EFFORT, LLM
from app.agent.tools import STATS_TOOLS, ApprovedPlan
from app.config import dataset_config, load_config
from app.science import contact_stats as cs
from app.science.contacts import CUTOFF_MAX, CUTOFF_MIN, SEQ_EXCLUSION_MAX
from app.science.loader import TRAJ_LOCK, load_universe

MAX_STEPS = 5
PLAN_TOOLS = ["get_metadata", "list_groups", "calculate_contacts", "distance_over_time", "compare_intervals", "contact_statistics",
              "cutoff_sensitivity", "native_contacts", "submit_report"]


class PlanInvalid(ValueError):
    def __init__(self, errors: list[str], plan: dict | None):
        super().__init__("; ".join(errors))
        self.errors, self.plan = errors, plan


def _window_schema() -> dict:
    return {"type": "array", "items": {"type": "integer"}, "description": "[start, stop) frame indices"}


def plan_schema() -> dict:
    cfg = load_config()
    groups = sorted({g for d in cfg["datasets"].values() for g in d["domains"]})
    return {"name": "Plan", "schema": {
        "type": "object",
        "properties": {
            "summary": {"type": "string", "description": "One sentence: what the plan will establish."},
            "dataset": {"type": "string", "enum": sorted(cfg["datasets"])},
            "group_a": {"type": "string", "enum": groups},
            "group_b": {"type": "string", "enum": groups},
            "cutoff": {"type": "number"},
            "window_a": _window_schema(),
            "window_b": {"anyOf": [_window_schema(), {"type": "null"}]},
            "stride": {"type": "integer"},
            "sequence_exclusion": {"type": "integer"},
            "robustness_cutoffs": {"type": "array", "items": {"type": "number"}},
            "steps": {"type": "array", "items": {
                "type": "object",
                "properties": {"tool": {"type": "string", "enum": PLAN_TOOLS}, "rationale": {"type": "string"}},
                "required": ["tool", "rationale"], "additionalProperties": False}},
        },
        "required": ["summary", "dataset", "group_a", "group_b", "cutoff", "window_a", "window_b", "stride",
                     "sequence_exclusion", "robustness_cutoffs", "steps"],
        "additionalProperties": False,
    }}


def dataset_facts() -> dict:
    out = {}
    for ds, d in load_config()["datasets"].items():
        with TRAJ_LOCK:
            n = load_universe(ds).trajectory.n_frames
        out[ds] = {"n_frames": n, "dt_ns": d["dt_ns"], "stats_valid": d["stats_valid"],
                   "groups": d["domains"], "description": d["description"].strip(),
                   "sequence_exclusion": load_config()["sequence_exclusion"]}
    return out


def validate_plan(p: dict) -> list[str]:
    """Code checks on a (model- or user-edited) plan. Returns a list of problems."""
    errs = []
    try:
        cfg = dataset_config(p.get("dataset", ""))
    except ValueError as e:
        return [str(e)]
    for g in ("group_a", "group_b"):
        if p.get(g) not in cfg["domains"]:
            errs.append(f"{g} {p.get(g)!r} is not a group of {p['dataset']} ({sorted(cfg['domains'])})")
    if p.get("group_a") == p.get("group_b"):
        errs.append("group_a and group_b must differ")
    try:
        c = float(p.get("cutoff"))
        if not CUTOFF_MIN <= c <= CUTOFF_MAX:
            errs.append(f"cutoff must be between {CUTOFF_MIN} and {CUTOFF_MAX} Å, got {c}")
    except (TypeError, ValueError):
        errs.append("cutoff must be a number")
        c = None
    with TRAJ_LOCK:
        n = load_universe(p["dataset"]).trajectory.n_frames
    wins = [("window_a", p.get("window_a"))] + ([("window_b", p["window_b"])] if p.get("window_b") else [])
    for name, w in wins:
        if not (isinstance(w, list) and len(w) == 2 and all(isinstance(x, int) for x in w) and 0 <= w[0] < w[1] <= n):
            errs.append(f"{name} must be [start, stop) with 0 <= start < stop <= {n}, got {w}")
    if len(wins) == 2 and not errs:
        (_, a), (_, b) = wins
        if a[0] < b[1] and b[0] < a[1]:
            errs.append(f"window_a {a} and window_b {b} overlap")
    if not isinstance(p.get("stride"), int) or p["stride"] < 1:
        errs.append("stride must be an integer >= 1")
    if not isinstance(p.get("sequence_exclusion"), int) or not 0 <= p["sequence_exclusion"] <= SEQ_EXCLUSION_MAX:
        errs.append(f"sequence_exclusion must be 0..{SEQ_EXCLUSION_MAX}")
    try:
        cs.validate_robustness_cutoffs(p.get("robustness_cutoffs") or [], c)
    except ValueError as e:
        errs.append(str(e))
    steps = p.get("steps") or []
    tools = [s.get("tool") for s in steps]
    if not 1 <= len(steps) <= MAX_STEPS:
        errs.append(f"the plan must have 1-{MAX_STEPS} steps")
    if tools and tools[-1] != "submit_report":
        errs.append("the last step must be submit_report")
    unknown = [t for t in tools if t not in PLAN_TOOLS]
    if unknown:
        errs.append(f"unknown tools {unknown}")
    if not cfg["stats_valid"]:
        bad = sorted(set(tools) & STATS_TOOLS)
        if bad:
            errs.append(f"{p['dataset']} has stats_valid: false, so {bad} are not allowed: {cfg['stats_refusal'].strip()}")
    needs_two = {"compare_intervals", "cutoff_sensitivity"} & set(tools)
    if needs_two and not p.get("window_b"):
        errs.append(f"{sorted(needs_two)} need two windows (window_b)")
    return errs


def to_approved(p: dict) -> ApprovedPlan:
    errs = validate_plan(p)
    if errs:
        raise PlanInvalid(errs, p)
    return ApprovedPlan(
        dataset=p["dataset"], group_a=p["group_a"], group_b=p["group_b"], cutoff=float(p["cutoff"]),
        window_a=list(p["window_a"]), window_b=list(p["window_b"]) if p.get("window_b") else None,
        stride=p["stride"], sequence_exclusion=p["sequence_exclusion"],
        robustness_cutoffs=cs.validate_robustness_cutoffs(p["robustness_cutoffs"], p["cutoff"]),
        steps=[{"tool": s["tool"], "rationale": s["rationale"]} for s in p["steps"]],
    )


def make_plan(llm: LLM, question: str, dataset: str) -> tuple[dict, list]:
    """Returns (plan dict, usages). One retry with the validation errors."""
    history = [{"role": "system", "content": prompts.PLANNER_SYSTEM},
               {"role": "user", "content": prompts.planner_user(question, dataset, dataset_facts())}]
    usages = []
    for attempt in range(2):
        r = llm.run_turn(history, schema=plan_schema(), effort=EFFORT["plan"])
        usages.append(r.usage)
        plan = r.parsed
        errs = validate_plan(plan) if isinstance(plan, dict) else ["the plan is not a JSON object"]
        if not errs:
            return plan, usages
        history += r.output_items + [{"role": "user", "content": "The plan is invalid:\n- " + "\n- ".join(errs)
                                      + "\nReturn a corrected plan."}]
    raise PlanInvalid(errs, plan)
