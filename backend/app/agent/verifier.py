"""Model check of an accepted report (SPEC §9 step 7): one Muse Wonder call, after acceptance, in the background.

The verifier sees the report text, each finding with the stored values it cites, and one matplotlib PNG per
cited result, and flags claims the data or plot does not support. The loop never waits on it. A major flag
makes the run "flagged"; otherwise "verified". The user still gives the final sign-off.

plant_overclaim() is the dev-only demo: it adds a finding that calls an unsupported pair supported and robust,
after the deterministic check has accepted the report, so only the model check can catch it.
"""
from __future__ import annotations

import json
import os
import threading
import time

from app.agent import prompts
from app.agent.llm import EFFORT, LLM, CostTracker
from app.agent.report import PLACEHOLDER, render
from app.agent.tools import jsonable, res_label
from app.config import dataset_config
from app.provenance.plots import plot_result, png_b64
from app.safety import budget

MAX_FIGURES = 6
MAX_VALUES = 40  # values per cited result in the verifier's input
SCHEMA = {"name": "verification", "schema": {
    "type": "object", "additionalProperties": False, "required": ["summary", "flags"],
    "properties": {
        "summary": {"type": "string"},
        "flags": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["finding_id", "quote", "problem", "plain", "severity"],
            "properties": {
                "finding_id": {"type": "string"},
                "quote": {"type": "string"},
                "problem": {"type": "string"},
                "plain": {"type": "string"},
                "severity": {"type": "string", "enum": ["major", "minor"]},
            }}},
    }}}


def planting_allowed() -> bool:
    """The overclaim toggle exists only where ALLOW_PLANTED_OVERCLAIM=1 (never set on a deploy)."""
    return os.environ.get("ALLOW_PLANTED_OVERCLAIM") == "1"


def verifier_llm() -> LLM:
    """Muse Wonder through the Meta Model API, whatever the agent's provider; its cost is logged separately."""
    return LLM("meta", model=os.environ.get("META_VERIFIER_MODEL", "muse-wonder-1.0"),
               tracker=CostTracker(on_usage=budget.record))


# ---------- planted overclaim (dev only) ----------

def plant_overclaim(run) -> str | None:
    """Add a finding calling the largest unsupported change 'significant and robust'. Returns its id, or None
    when the run has no unsupported tested pair to misdescribe."""
    state = run.state
    best = None
    for pid, res in state.results.items():
        if res["tool"] != "compare_intervals" or res["status"] != "ok":
            continue
        for pair, p in res["pairs"].items():
            if not p.get("supported") and (best is None or abs(p["delta"]) > abs(best[2])):
                best = (pid, pair, p["delta"])
    if best is None or not state.report:
        return None
    pid, pair, delta = best
    word = "drops" if delta < 0 else "rises"
    text = (f"The {pair} contact {word} significantly between the two windows "
            f"({{{{{pid}.{pair}.delta}}}}), a robust, supported change.")
    fid = state.next_id("f", state.findings)
    state.findings[fid] = {"id": fid, "text": text, "evidence_ids": [f"{pid}.{pair}"], "claim": "supported"}
    rendered, segments = render(state, text)
    state.report["findings"].append({**state.findings[fid], "rendered": rendered, "segments": segments,
                                     "badge": "supported", "badge_label": "supported", "pairs": [pair], "robustness": []})
    run.planted = fid
    return fid


# ---------- what the verifier sees ----------

def _pair_facts(res: dict, pair: str) -> dict:
    """The stored numbers for one cited pair, rounded, from the full result rather than the model's summary."""
    d = res["data_for_ui"] or {}
    for r in d.get("rows", []):
        if f"{res_label(r['key_a'])}-{res_label(r['key_b'])}" != pair:
            continue
        if res["tool"] == "cutoff_sensitivity":
            return {"verdict": r.get("verdict"), "holds_at": r.get("holds_at"),
                    "per_cutoff": [{k: c.get(k) for k in ("cutoff", "delta", "ci_low", "ci_high", "q", "supported")}
                                   for c in r.get("per_cutoff", [])]}
        keys = ("freq_a", "freq_b", "delta", "ci_low", "ci_high", "q", "supported", "provisional", "n_blocks_a",
                "n_blocks_b", "too_few_events", "too_few_blocks")
        return {k: r.get(k) for k in keys if k in r}
    return dict(res["pairs"].get(pair, {}))


def _round(x):
    if isinstance(x, float):
        return float(f"{x:.4g}")
    if isinstance(x, dict):
        return {k: _round(v) for k, v in x.items() if not k.startswith("key_")}
    if isinstance(x, list):
        return [_round(v) for v in x]
    return x


def verifier_input(run) -> dict:
    """The JSON document the verifier reads (findings with resolved values and the stored evidence)."""
    state, rep = run.state, run.state.report
    cfg = dataset_config(state.plan.dataset)
    findings = []
    for f in rep["findings"]:
        evidence = []
        for e in f["evidence_ids"]:
            pid, _, pair = e.partition(".")
            res = state.results.get(pid)
            if res is None:
                continue
            item = {"id": e, "tool": res["tool"], "status": res["status"]}
            if pair:
                item["values"] = _round(_pair_facts(res, pair))
            else:  # result-level values, plus every value of this result the finding's text uses
                raw = state.findings.get(f["id"], f)  # the finding as recorded (plain is still a placeholder string)
                used = [m[1].partition(".")[2] for m in PLACEHOLDER.finditer(f"{raw['text']} {raw.get('plain') or ''}")
                        if m[1].partition(".")[0] == pid]
                names = [k for k in res["values"] if "." not in k or k in used][:MAX_VALUES]
                item["values"] = _round({k: res["values"][k]["v"] for k in names})
            evidence.append(item)
        findings.append({"id": f["id"], "claim": f["claim"], "text": f["rendered"], "badge": f["badge_label"],
                         "robustness": [r["label"] for r in f.get("robustness", [])], "evidence": evidence})
    return {
        "question": run.question,
        "dataset": {"id": state.plan.dataset, "stats_valid": cfg["stats_valid"], "time": cfg["time_label"]},
        "plan": {k: v for k, v in state.plan.as_dict().items() if k != "steps"},
        "rules": {"supported": "BH q < 0.05 and CI excludes 0 and enough events and >= 10 chunks per window",
                  "provisional": "fewer than 40 independent chunks in either window",
                  "robust": "supported at every approved robustness cutoff with the same sign"},
        "findings": findings,
        **{k: [x["text"] for x in rep.get(k, [])] for k in ("hypotheses", "caveats", "gaps", "background")},
    }


# ---------- one PNG per cited result ----------

def figures(run) -> list[tuple[str, str]]:
    """(provenance id, base64 PNG) for each result the report's findings cite, at most MAX_FIGURES."""
    state = run.state
    tl = dataset_config(state.plan.dataset)["time_label"]
    pids = []
    for f in state.report["findings"]:
        for e in f["evidence_ids"]:
            pid = e.partition(".")[0]
            if pid in state.results and pid not in pids:
                pids.append(pid)
    out = []
    for pid in pids[:MAX_FIGURES]:
        fig = plot_result(state.results[pid], tl)
        if fig is not None:
            out.append((pid, png_b64(fig)))
    return out


# ---------- the call ----------

TIMEOUT_S = 120  # after this the report says the check couldn't finish (red-team E: no endless "checking...")
_settle_lock = threading.Lock()


def pending() -> dict:
    return {"status": "pending", "started_at": time.time()}


def timed_out() -> dict:
    return {"status": "timeout", "error": f"The check did not finish within {TIMEOUT_S // 60} minutes."}


def settle(run, verification: dict, status: str | None = None) -> bool:
    """Record the outcome once: a check that finishes after it was declared timed out changes nothing."""
    with _settle_lock:
        if (run.verification or {}).get("status") not in (None, "pending"):
            return False
        run.verification = verification
        if status:
            run.status = status
        return True


def resolve_stale(verification: dict | None, live: bool) -> dict | None:
    """A saved 'pending' check with no live thread behind it (the server restarted), or one past the limit,
    is shown as timed out instead of spinning forever."""
    if (verification or {}).get("status") == "pending":
        age = time.time() - (verification.get("started_at") or 0)
        if not live or age > TIMEOUT_S + 30:
            return timed_out()
    return verification


def verify_run(run, llm: LLM | None = None) -> dict:
    """Run the model check on an accepted report; sets run.verification and run.status (verified / flagged)."""
    t0 = time.perf_counter()
    if (run.verification or {}).get("status") != "pending":
        run.verification = pending()
    try:
        llm = llm or verifier_llm()
        doc = verifier_input(run)
        content = [{"type": "input_text", "text": json.dumps(jsonable(doc), ensure_ascii=False)}]
        figs = figures(run)
        for pid, png in figs:
            content += [{"type": "input_text", "text": f"Plot of {pid}:"},
                        {"type": "input_image", "image_url": f"data:image/png;base64,{png}"}]
        resp = llm.run_turn([{"role": "system", "content": prompts.VERIFIER_SYSTEM},
                             {"role": "user", "content": content}], schema=SCHEMA, effort=EFFORT["verify"])
        out = resp.parsed or {"summary": "", "flags": []}
        flags = [fl for fl in out["flags"] if isinstance(fl, dict)]
        status = "flagged" if any(fl["severity"] == "major" for fl in flags) else "verified"
        settle(run, {"status": status, "summary": out["summary"], "flags": flags, "figures": [p for p, _ in figs],
                     "usage": resp.usage.as_dict(), "wall_s": round(time.perf_counter() - t0, 2)}, status)
    except Exception as e:  # a failed check is shown as "could not be checked", never as verified
        err = f"{type(e).__name__}: {e}"
        print(f"[verifier] run {run.run_id}: check could not run: {err}", flush=True)  # API errors carry no key
        settle(run, {"status": "error", "error": err, "wall_s": round(time.perf_counter() - t0, 2)})
    return run.verification
