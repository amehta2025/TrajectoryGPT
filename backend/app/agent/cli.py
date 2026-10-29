"""CLI quick check (SPEC §9b): question -> plan -> (edit) -> run, one line per turn, then the report.

  cd backend
  python -m app.agent.cli run --turns 8 "Which LID-CORE contacts change between the first and second half?"
  python -m app.agent.cli run --cutoff 5.0 --robust 4.0,4.5,5.0 "..."     # edit the plan before it runs

Uses META_MODEL from .env (contributor in development) and aborts a run whose estimated cost passes --budget.
"""
import argparse
import json
import sys
import time

# Windows consoles default to cp1252, which cannot print "−" or "Å": force UTF-8 before any output.
for _stream in (sys.stdout, sys.stderr):
    _stream.reconfigure(encoding="utf-8", errors="replace")

from app.agent.llm import LLM, BudgetExceeded, CostTracker
from app.agent.orchestrator import MAX_TURNS, new_run, run_investigation
from app.agent.planner import PlanInvalid, make_plan, to_approved

DEV_BUDGET_USD = 0.50


def _cost(c) -> str:
    return "n/a" if c is None else f"${c:.4f}"


def _print_turn(e: dict) -> None:
    if e["type"] == "turn":
        pid = f" -> {e['provenance_id']}" if e.get("provenance_id") else ""
        print(f"  T{e['turn']:<2} {str(e['tool']):<19} {e['status']:<12} model {e['model_s']:5.1f}s ({e['effort']})"
              f"  tool {e['tool_s']:5.1f}s  {_cost(e['cost_usd'])}{pid}   [{e['progress']}]", flush=True)


def _print_report(run) -> None:
    rep = run.state.report
    if not rep:
        print(f"\nStatus: {run.status}" + (f" - {run.error}" if run.error else ""))
        for p in run.problems:
            print(f"  problem: {p}")
        return
    print("\n=== Report ===")
    for f in rep["findings"]:
        rob = "; ".join(f"{r['pair']}: {r['label']}" for r in f["robustness"])
        print(f"[{f['badge_label']}] {f['rendered']}" + (f"   <{rob}>" if rob else ""))
        print(f"    citations: {', '.join(f['evidence_ids'])}")
    if rep["statistical_support"]:
        print("Statistical support:")
        for s in rep["statistical_support"]:
            print(f"  {s['pair']}: Δ {s['delta']}  CI {s['ci']}  q {s['q']}  chunks {s['n_blocks_a']}/{s['n_blocks_b']}"
                  f"  τ {s['tau_ns_a']}/{s['tau_ns_b']}  {'supported' if s['supported'] else 'not supported'}"
                  f"{' (provisional)' if s['provisional'] else ''}  [{s['robustness']}]")
    for x in rep["background"]:
        print(f"[{rep['background_label']}] {x['text']}")
    for name in ("hypotheses", "caveats", "gaps"):
        if rep[name]:
            print(name.capitalize() + ":")
            for x in rep[name]:
                print(f"  - {x['text']}")
    if rep["follow_ups"]:
        print("Follow-ups:")
        for fu in rep["follow_ups"]:
            print(f"  - {fu.get('tool')} {fu.get('pair', '')}: {fu.get('rationale')}")


def _print_timing(run, plan_s: float) -> None:
    model = sum(t["model_s"] for t in run.turns)
    tool = sum(t["tool_s"] for t in run.turns)
    plan_model = sum(u["latency_s"] for u in run.plan_usage)
    total = plan_s + run.wall_s
    print("\n=== Timing (target < 90 s) ===")
    print(f"  plan:  {plan_s:5.1f}s (model {plan_model:.1f}s, effort {', '.join(u['effort'] for u in run.plan_usage)})")
    print(f"  loop:  {run.wall_s:5.1f}s (model {model:.1f}s, tools {tool:.1f}s, {len(run.turns)} turns, effort "
          f"{', '.join(sorted({t['usage']['effort'] for t in run.turns}))})")
    print(f"  total: {total:5.1f}s  {'OK' if total < 90 else 'OVER TARGET'}")
    calls = run.plan_usage + [t["usage"] for t in run.turns]
    print(f"  cost:  {_cost(run.cost_usd())} over {len(calls)} calls  (model {calls[0]['model']})")


def cmd_run(a) -> int:
    tracker = CostTracker(limit_usd=a.budget)
    llm = LLM(a.provider, tracker=tracker)
    print(f"Model {llm.model} ({llm.provider}); budget {_cost(a.budget)} per run")
    t0 = time.perf_counter()
    try:
        plan, usages = make_plan(llm, a.question, a.dataset)
    except PlanInvalid as e:
        print("Plan invalid after one retry:\n  - " + "\n  - ".join(e.errors))
        return 1
    except BudgetExceeded as e:
        print(f"Aborted: {e}")
        return 1
    plan_s = time.perf_counter() - t0
    for u in usages:
        print(f"  plan call: {u.latency_s:.1f}s effort={u.effort} in={u.input_tokens} out={u.output_tokens} {_cost(u.cost_usd)}")
    if a.cutoff is not None:
        plan["cutoff"] = a.cutoff
    if a.robust:
        plan["robustness_cutoffs"] = [float(x) for x in a.robust.split(",")]
    print(f"\nPlan: {plan['summary']}")
    print(json.dumps({k: v for k, v in plan.items() if k not in ("summary", "steps")}))
    for i, s in enumerate(plan["steps"], 1):
        print(f"  {i}. {s['tool']}: {s['rationale']}")
    try:
        approved = to_approved(plan)
    except PlanInvalid as e:
        print("Edited plan is invalid:\n  - " + "\n  - ".join(e.errors))
        return 1

    run = new_run(a.question, approved)
    run.plan_usage = [u.as_dict() for u in usages]
    print(f"\nRun {run.run_id}")
    run = run_investigation(llm, run, max_turns=a.turns, on_event=_print_turn)
    path = run.save()
    _print_report(run)
    _print_timing(run, plan_s)
    print(f"\nSaved {path}")
    return 0 if run.status == "awaiting_verification" else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.agent.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="plan and run one investigation")
    r.add_argument("question")
    r.add_argument("--turns", type=int, default=MAX_TURNS)
    r.add_argument("--dataset", default="adk_equilibrium")
    r.add_argument("--provider", choices=["meta", "baseten"], default=None)
    r.add_argument("--cutoff", type=float, help="edit the plan's main cutoff (Å)")
    r.add_argument("--robust", help="edit the robustness cutoffs, e.g. 4.0,4.5,5.0")
    r.add_argument("--budget", type=float, default=DEV_BUDGET_USD, help="abort when estimated $ passes this")
    a = ap.parse_args(argv)
    return cmd_run(a)


if __name__ == "__main__":
    sys.exit(main())
