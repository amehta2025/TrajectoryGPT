"""Model check of an accepted report (SPEC §9 step 7): one Muse Wonder call, after acceptance, in the background.

The verifier sees the report text, each finding with the stored values it cites, and one matplotlib PNG per
cited result, and flags claims the data or plot does not support. The loop never waits on it. A major flag
makes the run "flagged"; otherwise "verified". The user still gives the final sign-off.

plant_overclaim() is the dev-only demo: it adds a finding that calls an unsupported pair supported and robust,
after the deterministic check has accepted the report, so only the model check can catch it.
"""
from __future__ import annotations

import base64
import io
import json
import os
import time

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from app.agent import prompts  # noqa: E402
from app.agent.llm import EFFORT, LLM, CostTracker  # noqa: E402
from app.agent.report import render  # noqa: E402
from app.agent.tools import jsonable, res_label  # noqa: E402
from app.config import dataset_config  # noqa: E402
from app.safety import budget  # noqa: E402

MAX_FIGURES = 6
SCHEMA = {"name": "verification", "schema": {
    "type": "object", "additionalProperties": False, "required": ["summary", "flags"],
    "properties": {
        "summary": {"type": "string"},
        "flags": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["finding_id", "quote", "problem", "severity"],
            "properties": {
                "finding_id": {"type": "string"},
                "quote": {"type": "string"},
                "problem": {"type": "string"},
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
            else:
                item["values"] = _round({k: v["v"] for k, v in res["values"].items() if "." not in k})
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

def _png(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=80, bbox_inches="tight")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode()


def _plot(res: dict, time_label: str) -> str | None:
    d, tool, pid = res["data_for_ui"] or {}, res["tool"], res["provenance_id"]
    if tool == "compare_intervals" and d.get("rows"):
        rows = sorted(d["rows"], key=lambda r: -abs(r["delta"]))[:15][::-1]
        fig, ax = plt.subplots(figsize=(6, 0.3 * len(rows) + 1))
        for i, r in enumerate(rows):
            c = "tab:blue" if r["supported"] else "0.6"
            ax.errorbar(r["delta"], i, xerr=[[r["delta"] - r["ci_low"]], [r["ci_high"] - r["delta"]]], fmt="o", color=c)
        ax.set_yticks(range(len(rows)), [f"{res_label(r['key_a'])}-{res_label(r['key_b'])}  q={r['q']:.2g}" for r in rows])
        ax.axvline(0, color="k", lw=0.8)
        ax.set_xlabel("Δ contact frequency (window B − A), 95% CI; blue = supported, grey = not supported")
    elif tool == "cutoff_sensitivity" and d.get("rows"):
        fig, ax = plt.subplots(figsize=(6, 3))
        for r in d["rows"][:6]:
            pc = [c for c in r["per_cutoff"] if c.get("tested")]
            ax.errorbar([c["cutoff"] for c in pc], [c["delta"] for c in pc],
                        yerr=[[c["delta"] - c["ci_low"] for c in pc], [c["ci_high"] - c["delta"] for c in pc]],
                        marker="o", capsize=3, label=f"{res_label(r['key_a'])}-{res_label(r['key_b'])}: {r['verdict']}")
        ax.axhline(0, color="k", lw=0.8)
        ax.set_xlabel("cutoff (Å)")
        ax.set_ylabel("Δ frequency, 95% CI")
        ax.legend(fontsize=7)
    elif tool in ("calculate_contacts", "contact_difference_map") and (d.get("frequency") or d.get("delta")):
        m = np.array(d.get("frequency") or d.get("delta"), dtype=float)
        fig, ax = plt.subplots(figsize=(6, 4))
        im = ax.imshow(m, aspect="auto", cmap="viridis" if tool == "calculate_contacts" else "RdBu_r",
                       **({} if tool == "calculate_contacts" else {"vmin": -1, "vmax": 1}))
        fig.colorbar(im, ax=ax, label="contact frequency" if tool == "calculate_contacts" else "Δ frequency (B − A)")
        ax.set_xlabel(f"{d.get('group_b')} residue index")
        ax.set_ylabel(f"{d.get('group_a')} residue index")
    elif tool == "distance_over_time" and d.get("distance"):
        fig, ax = plt.subplots(figsize=(6, 3))
        ax.plot(d["frames"], d["distance"], lw=0.6)
        ax.set_xlabel("frame")
        ax.set_ylabel("min heavy-atom distance (Å)")
    elif tool == "contact_statistics" and d.get("in_contact"):
        x = np.array(d["in_contact"], dtype=float)
        w = max(1, len(x) // 50)
        fig, ax = plt.subplots(figsize=(6, 3))
        ax.plot(d["frames"][: len(x) - w + 1], np.convolve(x, np.ones(w) / w, mode="valid"), lw=0.8)
        ax.set_xlabel("frame")
        ax.set_ylabel(f"in contact (running mean of {w} frames)")
    elif tool in ("native_contacts", "reproduction_check") and (d.get("q") or d.get("ours")):
        q = np.array(d.get("q") or d.get("ours"), dtype=float)
        fig, ax = plt.subplots(figsize=(6, 3))
        for j in range(q.shape[1]):
            ax.plot(d["frames"], q[:, j], label=f"Q{j + 1}")
        ax.set_xlabel(time_label)
        ax.set_ylabel("fraction of native contacts")
        ax.legend()
    elif tool == "residue_flexibility" and d.get("rmsf"):
        fig, ax = plt.subplots(figsize=(6, 3))
        ax.plot(d["resids"], d["rmsf"], lw=0.8)
        ax.set_xlabel("residue")
        ax.set_ylabel("RMSF (Å)")
    else:
        return None
    ax.set_title(f"{pid}: {tool}", fontsize=9)
    return _png(fig)


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
        png = _plot(state.results[pid], tl)
        if png:
            out.append((pid, png))
    return out


# ---------- the call ----------

def verify_run(run, llm: LLM | None = None) -> dict:
    """Run the model check on an accepted report; sets run.verification and run.status (verified / flagged)."""
    t0 = time.perf_counter()
    run.verification = {"status": "pending"}
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
        run.verification = {"status": status, "summary": out["summary"], "flags": flags, "figures": [p for p, _ in figs],
                            "usage": resp.usage.as_dict(), "wall_s": round(time.perf_counter() - t0, 2)}
        run.status = status
    except Exception as e:  # a failed check is shown as "could not be checked", never as verified
        run.verification = {"status": "error", "error": f"{type(e).__name__}: {e}",
                            "wall_s": round(time.perf_counter() - t0, 2)}
    return run.verification
