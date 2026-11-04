"""Systematic sweep of the app's flows against a running backend (local dev server or the deploy).

Each case goes through the HTTP API the way the UI does (plan, figure read + plan, run, follow-ups with the same
session id) and is classified:
  pass       report accepted (or an expected refusal: unsupported figure, off-topic or impossible question)
  fallback   report built from computed results because the AI's wording failed the checks (counted apart)
  fail       needs_review, error, unexpected refusal, planning failure, timeout, or (with the verifier) flagged
Results go to cache/sweep/<label>.json (gitignored) and a one-line-per-case table is printed.

Run (repo root, Git Bash or PowerShell), with the backend-sweep server from .claude/launch.json running:
  .venv/Scripts/python backend/scripts/sweep.py --label baseline
  .venv/Scripts/python backend/scripts/sweep.py --label final --only demo --base https://<deploy>   # production
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.stdout.reconfigure(encoding="utf-8")
FIG = ROOT / "frontend" / "public" / "figures"
DEMO = ROOT / "frontend" / "public" / "_local_demo"  # the demo papers (not in the repo)
OUT = ROOT / "cache" / "sweep"
EQ, TR = "adk_equilibrium", "adk_transition"
FU = ["Do any of these contacts change between the first and second half?", "Which of these is strongest?"]

# (id, kind, payload, dataset, expect, provider, follow_ups)   expect: report | declined | unsupported
DEMO_CASES = [
    ("demo1", "q", "Which LID–CORE contacts change between the first and second half?", EQ, "report", None, False),
    ("demo2", "q", "Is the SER30–ASP84 drop real?", EQ, "report", None, False),
    ("demo3", "q", "Reproduce the native contacts figure", TR, "report", None, False),
]
CASES = DEMO_CASES + [
    ("q_lidcore_plain", "q", "Which contacts between the lid and the core change between the first and second half?", EQ, "report", None, True),
    ("q_touch", "q", "Do NMP and LID touch?", EQ, "report", None, False),
    ("q_flex", "q", "How flexible is the LID domain?", EQ, "report", None, False),
    ("q_explain", "q", "Explain this simulation.", EQ, "report", None, False),
    ("q_pair_time", "q", "what happens to HSE134-ASP118 over time", EQ, "report", None, True),
    ("q_opening", "q", "How open is the LID domain over the run?", EQ, "report", None, False),
    ("q_nmp_vs_core", "q", "Does the NMP domain move more than the CORE?", EQ, "report", None, False),
    ("q_typo", "q", "lid core contacts chnage first vs second half?", EQ, "report", None, False),
    ("q_pair_stable", "q", "Is the contact between Thr155 and Gln160 stable?", EQ, "report", None, False),
    ("q_long", "q", "Which NMP–CORE contacts change between the first and second half of the 1 µs run, and are any of "
                    "them robust to the choice of distance cutoff? Please be careful about noise.", EQ, "report", None, False),
    ("q_vague", "q", "contacts?", EQ, "report", None, False),
    ("q_bad_residue", "q", "Does residue 300 contact the LID?", EQ, "declined", None, False),
    ("q_offtopic", "q", "What's the capital of France?", EQ, "declined", None, False),
    ("q_yesno", "q", "Is the LID more mobile than the NMP?", EQ, "report", None, False),
    ("q_short_pair", "q", "SER30 ASP84?", EQ, "report", None, False),
    ("tr_touch", "q", "Do NMP and LID touch along the path?", TR, "report", None, False),
    ("tr_halves", "q", "Which LID–CORE contacts change between the first and second half?", TR, "report", None, False),
    ("fig_native_sample", "sample_native", None, TR, "report", None, False),
    ("fig_iyer_sample", "sample_iyer", None, EQ, "report", None, False),
    ("fig_01", "pdf", ("01_contact_map_adk.pdf", ""), EQ, "report", None, True),
    ("fig_02", "pdf", ("02_difference_map_adk.pdf", ""), EQ, "report", None, True),
    ("fig_04", "pdf", ("04_rmsf_adk.pdf", ""), EQ, "report", None, False),
    ("fig_05", "pdf", ("05_native_contacts_exampase.pdf", ""), EQ, "report", None, False),
    ("fig_07_no_methods", "pdf", ("07_contact_map_NO_METHODS.pdf", ""), EQ, "report", None, False),
    ("fig_06_unsupported", "pdf", ("06_rmsd_UNSUPPORTED.pdf", ""), EQ, "unsupported", None, False),
    ("fig_01_note", "pdf", ("01_contact_map_adk.pdf", FU[0]), EQ, "report", None, False),
    ("fig_04_note", "pdf", ("04_rmsf_adk.pdf", "Focus on the LID domain."), EQ, "report", None, False),
    ("open_demo1", "q", "Which LID–CORE contacts change between the first and second half?", EQ, "report", "baseten", False),
    ("open_touch", "q", "Do NMP and LID touch?", EQ, "report", "baseten", False),
    ("open_fig_01", "pdf", ("01_contact_map_adk.pdf", ""), EQ, "report", "baseten", False),
]


class Client:
    def __init__(self, base: str, verify: bool):
        self.base, self.verify = base.rstrip("/"), verify

    def call(self, method: str, path: str, sid: str, **kw) -> httpx.Response:
        with httpx.Client(timeout=300) as c:
            return c.request(method, self.base + path, headers={"X-Session-Id": sid}, **kw)


def upload(path: Path, mime: str) -> dict:
    return {"filename": path.name, "mime": mime, "data": base64.b64encode(path.read_bytes()).decode()}


def wait_run(cl: Client, sid: str, run_id: str) -> dict:
    t0 = time.time()
    while time.time() - t0 < 420:
        v = cl.call("GET", f"/runs/{run_id}", sid).json()
        settled = v["status"] not in ("running", "pending")
        if settled and (not cl.verify or v["status"] != "awaiting_verification" or (v.get("verification") or {}).get("status") not in (None, "pending")):
            return v
        time.sleep(3)
    return {"status": "timeout", "run_id": run_id}


def classify(v: dict, expect: str, verify: bool) -> tuple[str, str]:
    rep = v.get("report") or {}
    if v["status"] == "timeout":
        return "fail", "timed out"
    if v["status"] in ("needs_review", "error", "aborted_budget"):
        return "fail", f"{v['status']}: {'; '.join(v.get('problems') or [v.get('error') or ''])[:300]}"
    if verify and (v.get("verification") or {}).get("status") == "flagged":
        flags = "; ".join(f["problem"][:120] for f in v["verification"].get("flags", []) if f.get("severity") == "major")
        return "fail", f"flagged: {flags}"
    if rep.get("fallback"):
        return "fallback", "; ".join(rep["fallback"]["removed"])[:300]
    if expect != "report":
        return "fail", f"expected {expect}, got a report"
    ver = (v.get("verification") or {}).get("status")
    return "pass", f"verified" if ver == "verified" else (ver or "accepted")


def summary(v: dict) -> dict:
    p = v.get("plan") or {}
    ci = next((r["data_for_ui"] for r in (v.get("results") or {}).values() if r["tool"] == "compare_intervals" and r["status"] == "ok"), None)
    rc = next((r["data_for_ui"] for r in (v.get("results") or {}).values() if r["tool"] == "reproduction_check" and r["status"] == "ok"), None)
    rep = v.get("report") or {}
    return {"run_id": v.get("run_id"), "dataset": p.get("dataset"), "cutoff": p.get("cutoff"), "seq": p.get("sequence_exclusion"),
            "ranges": p.get("residue_ranges"), "windows": [p.get("window_a"), p.get("window_b")],
            "steps": [s["tool"] for s in p.get("steps", [])], "tested": f"{ci['n_supported']}/{ci['n_tested']}" if ci else None,
            "repro": f"{'match' if rc['match'] else 'no match'} {rc['n_values']}" if rc else None,
            "headline": ((rep.get("headline") or {}).get("text") or "")[:160], "cost": v.get("cost_usd"),
            "verifier_cost": ((v.get("verification") or {}).get("usage") or {}).get("cost_usd")}


def plan_and_run(cl: Client, sid: str, case_id: str, kind: str, payload, dataset: str, provider: str | None,
                 follow_up: bool = False) -> dict:
    t0 = time.time()
    if kind == "q":
        r = cl.call("POST", "/plan", sid, json={"question": payload, "dataset": dataset, "session_id": sid,
                                                 "follow_up": follow_up, "provider": provider})
        if r.status_code != 200:
            return {"stage": "plan", "http": r.status_code, "detail": r.text[:300]}
        prop = r.json()
        if prop.get("declined"):
            return {"stage": "declined", "detail": prop["declined"].get("message", "")[:200]}
    else:
        if kind == "sample_native":
            files, note = [upload(FIG / "userguide_q1q2_replot.png", "image/png")], "Reproduce this figure."
        elif kind == "sample_iyer":
            files = [upload(FIG / "iyer2020_fig5c_adk_difference_contact_map.png", "image/png")]
            note = "Apply this figure's method to our simulation. " + (FIG / "iyer2020_fig5c_caption.txt").read_text(encoding="utf-8")
        else:
            name, note = payload
            files = [upload(DEMO / name, "application/pdf")]
        r = cl.call("POST", "/figure/read", sid, json={"files": files, "note": note[:8000], "provider": provider})
        if r.status_code != 200:
            d = r.json().get("detail") if r.headers.get("content-type", "").startswith("application/json") else r.text
            if isinstance(d, dict) and d.get("unsupported"):
                return {"stage": "unsupported", "detail": d["unsupported"][:200]}
            return {"stage": "figure_read", "http": r.status_code, "detail": str(d)[:300]}
        read = r.json()
        r = cl.call("POST", f"/figure/read/{read['read_id']}/plan", sid, json={"dataset": dataset})
        if r.status_code != 200:
            return {"stage": "figure_plan", "http": r.status_code, "detail": r.text[:300]}
        prop = r.json()
    plan_s = round(time.time() - t0, 1)
    fig = prop.get("figure") or {}
    confirmed = [f["name"] for f in (fig.get("spec") or {}).get("fields", []) if f["confidence"] == "low"]
    edits = {}
    if (fig.get("spec") or {}).get("foreign_residues"):
        edits["pair"] = "ASP54-LYS157"  # the harness picks an AdK pair, as a user would
    r = cl.call("POST", "/runs", sid, json={"plan_id": prop["plan_id"], "edits": edits, "confirmed_fields": confirmed})
    if r.status_code != 200:
        return {"stage": "run_start", "http": r.status_code, "detail": r.text[:300]}
    v = wait_run(cl, sid, r.json()["run_id"])
    return {"stage": "ran", "view": v, "plan_s": plan_s, "run_s": round(time.time() - t0 - plan_s, 1)}


def run_case(cl: Client, case) -> list[dict]:
    cid, kind, payload, dataset, expect, provider, follow_ups = case
    sid = uuid.uuid4().hex
    rows = []

    def record(name, out, exp):
        if out["stage"] == "ran":
            outcome, why = classify(out["view"], exp, cl.verify)
            rows.append({"case": name, "outcome": outcome, "why": why, **summary(out["view"]), "plan_s": out["plan_s"], "run_s": out["run_s"]})
        elif out["stage"] in ("declined", "unsupported"):
            ok = (out["stage"] == "declined" and exp == "declined") or (out["stage"] == "unsupported" and exp == "unsupported")
            rows.append({"case": name, "outcome": "pass" if ok else "fail", "why": f"{out['stage']}: {out['detail']}"})
        else:
            rows.append({"case": name, "outcome": "fail", "why": f"{out['stage']} HTTP {out.get('http')}: {out.get('detail')}"})
        return out

    first = record(cid, plan_and_run(cl, sid, cid, kind, payload, dataset, provider), expect)
    if follow_ups and first.get("stage") == "ran" and (first["view"].get("report")):
        parent = first["view"].get("plan") or {}
        for i, q in enumerate(FU, 1):
            out = record(f"{cid}+fu{i}", plan_and_run(cl, sid, cid, "q", q, dataset, provider, follow_up=True), "report")
            # "these contacts" refers to the previous run: same data, groups, cutoff and residue ranges.
            if out.get("stage") == "ran" and rows[-1]["outcome"] != "fail":
                child = out["view"].get("plan") or {}
                lost = [k for k in ("dataset", "cutoff", "residue_ranges") if child.get(k) != parent.get(k)]
                if {child.get("group_a"), child.get("group_b")} != {parent.get("group_a"), parent.get("group_b")}:
                    lost.append("groups")
                if lost:
                    rows[-1].update(outcome="fail", why=f"follow-up lost the previous run's {', '.join(lost)}: "
                                    + ", ".join(f"{k} {parent.get(k)} -> {child.get(k)}" for k in lost))
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--verify", action="store_true", help="wait for the second-AI check and count flags as failures")
    ap.add_argument("--only", default="", help="'demo' or a comma list of case ids")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--no-followups", default="", help="comma list of case ids to run without their follow-ups")
    a = ap.parse_args()
    cases = DEMO_CASES if a.only == "demo" else [c for c in CASES if not a.only or c[0] in a.only.split(",")]
    skip_fu = set(a.no_followups.split(","))
    cases = [c[:6] + (False,) if c[0] in skip_fu else c for c in cases]
    cl = Client(a.base, a.verify)
    OUT.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    with ThreadPoolExecutor(a.workers) as ex:
        for res in ex.map(lambda c: run_case(cl, c), cases):
            for r in res:
                rows.append(r)
                print(f"{r['case']:<24} {r['outcome']:<9} {r.get('tested') or r.get('repro') or '':<12} {r['why'][:150]}", flush=True)
    (OUT / f"{a.label}.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False), encoding="utf-8")
    n = len(rows)
    by = {k: sum(r["outcome"] == k for r in rows) for k in ("pass", "fallback", "fail")}
    cost = sum((r.get("cost") or 0) + (r.get("verifier_cost") or 0) for r in rows)
    print(f"\n{a.label}: {by['pass']}/{n} pass, {by['fallback']} fallback, {by['fail']} fail; run cost ${cost:.3f}")


if __name__ == "__main__":
    main()
