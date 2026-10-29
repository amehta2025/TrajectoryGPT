"""Findings, placeholders, the deterministic check, and the report (SPEC §4, §9 steps 6-7).

The model never writes numbers: it writes {{r2.HSE134-ASP118.delta}}, and code fills the value from the
stored tool result. The check runs inside the loop: record_finding verifies evidence ids and
placeholders; submit_report runs the full check and refuses with the specific problems.
"""
from __future__ import annotations

import re

from app.config import dataset_config

PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z0-9_.\-]+)\s*\}\}")
NUMBER = re.compile(r"(?<![A-Za-z0-9_.])[-+−]?\d+(?:\.\d+)?(?![0-9])")
# A number is allowed right after these words (residue numbers, frames, ranges like "residues 122-159").
ALLOWED_CONTEXT = re.compile(r"(?:\bresidues?|\bresids?|\bframes?|\bpath steps?)\s*(?:\d+\s*(?:[-–—]|to|and)\s*)?$", re.I)
SUPPORT_WORDS = re.compile(r"\b(supported|significant(?:ly)?|robust(?:ly)?)\b", re.I)
NEGATION = re.compile(r"(?:\bnot|\bno|\bun|n't|\bnor|\bwithout)\s*(?:\w+\s+){0,2}$", re.I)
TEXT_LIMIT = 1200

# claim -> (badge, label shown next to the finding)
CLAIMS = {
    "supported": ("proven", "proven"),
    "not_supported": ("unsupported", "not supported — consistent with noise"),
    "null": ("null", "no supported change found"),
    "descriptive": ("descriptive", "descriptive — not statistically tested"),
}
BACKGROUND_LABEL = "background — not computed"
UNTESTED_CAVEAT = ("This dataset is a biased, non-stationary transition path, so no statistics were computed; "
                   "findings here are descriptive only.")

STANDARD_CAVEATS = [
    "Single trajectory: these findings describe this one simulation, not the protein's behaviour in general.",
    "Limited sampling: frames are correlated, so the effective number of independent observations is much "
    "smaller than the frame count; intervals are autocorrelation-corrected but calibrated, not exact.",
]


# ---------- formatting ----------

def _num(x: float, nd: int = 3) -> str:
    return f"{x:.{nd}f}".replace("-", "−")


def format_value(v, kind: str, time_label: str = "ns") -> str:
    if v is None:
        return "n/a"
    if kind == "freq":
        return _num(v)
    if kind == "delta":
        return ("+" if v > 0 else "") + _num(v)
    if kind in ("ci", "ci_freq"):
        lo, hi = v
        return f"[{_num(lo)}, {_num(hi)}]"
    if kind == "q":
        return f"{v:.1e}".replace("-", "−") if v < 1e-3 else f"{v:.2g}"
    if kind == "tau_ns":
        return f"{v:.1f} ns"
    if kind == "chunks":
        return f"{v:.0f}"
    if kind in ("count", "frame"):
        return f"{int(v)}"
    if kind == "distance":
        return f"{v:.2f} Å"
    if kind == "cutoff_list":
        return ", ".join(f"{c:.1f}" for c in v) + " Å" if v else "no cutoff"
    if kind == "cutoff_range":
        return f"{v[0]:.1f}–{v[1]:.1f} Å"
    return str(v)


def resolve(state, path: str) -> dict | None:
    """'r2.HSE134-ASP118.delta' -> {"v", "kind", "pid", "text"}; None if it does not resolve."""
    pid, _, name = path.partition(".")
    res = state.results.get(pid)
    if res is None or res["status"] != "ok" or name not in res["values"]:
        return None
    val = res["values"][name]
    tl = dataset_config(state.plan.dataset)["time_label"]
    return {**val, "pid": pid, "path": path, "text": format_value(val["v"], val["kind"], tl)}


def render(state, text: str) -> tuple[str, list[dict]]:
    """Fill placeholders; returns (text, segments). Segments alternate plain text {"text"} and filled
    values {"text", "pid", "path", "kind", "v"} so the UI can show each value's provenance on hover."""
    segments, pos = [], 0
    for m in PLACEHOLDER.finditer(text):
        if m.start() > pos:
            segments.append({"text": text[pos:m.start()]})
        r = resolve(state, m[1])
        segments.append(r if r is not None else {"text": m[0]})
        pos = m.end()
    if pos < len(text):
        segments.append({"text": text[pos:]})
    return "".join(s["text"] for s in segments), segments


# ---------- individual checks ----------

def unresolved_placeholders(state, text: str) -> list[str]:
    return [m for m in PLACEHOLDER.findall(text) if resolve(state, m) is None]


def evidence_problems(state, ids: list[str]) -> list[str]:
    out = []
    for e in ids:
        pid, _, pair = e.partition(".")
        res = state.results.get(pid)
        if res is None:
            out.append(f"evidence {e!r} does not exist")
        elif pair and pair not in res["pairs"]:
            out.append(f"evidence {e!r}: result {pid} has no pair {pair}")
    return out


def bare_numbers(state, text: str) -> list[str]:
    """Numerals left after removing placeholders, except residue/frame numbers and approved cutoffs."""
    plan = state.plan
    cutoffs = {round(c, 3) for c in [plan.cutoff, *plan.robustness_cutoffs]}
    stripped = PLACEHOLDER.sub(" ", text)
    bad = []
    for m in NUMBER.finditer(stripped):
        before, after = stripped[:m.start()], stripped[m.end():]
        if ALLOWED_CONTEXT.search(before[-40:]):
            continue
        tok = m[0].replace("−", "-")
        if "." in tok and round(float(tok), 3) in cutoffs:
            continue
        if re.match(r"\s*(?:Å|A\b|angstrom)", after) and round(float(tok), 3) in cutoffs:
            continue
        bad.append(m[0])
    return bad


def _unnegated_support_words(text: str) -> list[str]:
    stripped = PLACEHOLDER.sub(" ", text)
    return [m[1] for m in SUPPORT_WORDS.finditer(stripped) if not NEGATION.search(stripped[:m.start()][-30:])]


def _cited_pairs(state, ids: list[str]) -> list[tuple[str, str, dict]]:
    out = []
    for e in ids:
        pid, _, pair = e.partition(".")
        res = state.results.get(pid)
        if res and pair and pair in res["pairs"]:
            out.append((pid, pair, res))
    return out


def robustness(state, pair: str) -> dict:
    """Latest cutoff_sensitivity verdict for a pair: robust / cutoff-sensitive / not checked."""
    for pid in reversed(list(state.results)):
        res = state.results[pid]
        if res["tool"] == "cutoff_sensitivity" and res["status"] == "ok" and pair in res["pairs"]:
            p = res["pairs"][pair]
            cuts = p["cutoffs"]
            if p["verdict"] == "robust":
                return {"verdict": "robust", "pid": pid, "label": f"robust {cuts[0]:.1f}–{cuts[-1]:.1f} Å"}
            held = ", ".join(f"{c:.1f}" for c in p["holds_at"]) or "none"
            return {"verdict": "cutoff-sensitive", "pid": pid, "label": f"cutoff-sensitive: holds at {held}"}
    return {"verdict": "not checked", "pid": None, "label": "robustness not checked"}


def finding_problems(state, fid: str, f: dict) -> list[str]:
    """Full deterministic check for one finding (SPEC §9 step 7)."""
    p = [f"{fid}: {x}" for x in evidence_problems(state, f["evidence_ids"])]
    p += [f"{fid}: placeholder {{{{{x}}}}} does not resolve" for x in unresolved_placeholders(state, f["text"])]
    nums = bare_numbers(state, f["text"])
    if nums:
        p.append(f"{fid}: bare numbers {nums} - use placeholders for every value")
    for e in f["evidence_ids"]:
        res = state.results.get(e.partition(".")[0])
        if res and res["status"] != "ok":
            p.append(f"{fid}: cites {e}, which was {res['status']}")
    cited = _cited_pairs(state, f["evidence_ids"])
    if f["claim"] == "supported":
        stat_pairs = [(pid, pair, res) for pid, pair, res in cited if "supported" in res["pairs"][pair]]
        if not stat_pairs:
            p.append(f"{fid}: a 'supported' claim must cite a pair from compare_intervals, e.g. 'r2.HSE134-ASP118'")
        for pid, pair, res in stat_pairs:
            if not res["pairs"][pair]["supported"]:
                p.append(f"{fid}: claims support but {pid}.{pair} is not supported")
    elif f["claim"] == "not_supported":
        for pid, pair, res in cited:
            if res["pairs"][pair].get("supported"):
                p.append(f"{fid}: says not supported but {pid}.{pair} is supported")
    elif f["claim"] == "null":
        # "No supported change" must cite the comparison that shows it: a compare_intervals result with none supported.
        comps = [state.results[e] for e in f["evidence_ids"]
                 if e in state.results and state.results[e]["tool"] == "compare_intervals"]
        if not comps:
            p.append(f"{fid}: a null finding must cite the compare_intervals result (e.g. 'r1') that shows no supported change")
        for res in comps:
            n = res["values"].get("n_supported", {}).get("v")
            if n:
                p.append(f"{fid}: claims no supported change but {res['provenance_id']} has supported pairs")
    words = _unnegated_support_words(f["text"])
    if f["claim"] == "descriptive" and words:
        p.append(f"{fid}: a descriptive finding is not statistically tested and cannot use {words}")
    elif f["claim"] == "not_supported":
        bad = [w for w in words if not w.lower().startswith("robust")]
        if bad:
            p.append(f"{fid}: uses {bad} without claim='supported'")
    elif f["claim"] == "null" and any(w.lower().startswith("robust") for w in words):
        p.append(f"{fid}: a null finding cannot call anything robust")
    if f["claim"] in ("supported", "not_supported") and any(w.lower().startswith("robust") for w in words):
        for _, pair, _ in cited:
            if robustness(state, pair)["verdict"] != "robust":
                p.append(f"{fid}: calls {pair} robust, but no cutoff_sensitivity result shows it robust")
    return p


# ---------- bookkeeping tools ----------

def update_checklist(state, args: dict) -> dict:
    item, status, note = args.get("item"), args.get("status"), (args.get("note") or "").strip()
    if not isinstance(item, int) or not 1 <= item <= len(state.checklist):
        return {"status": "refused", "message": f"item must be 1..{len(state.checklist)}"}
    if status not in ("in_progress", "done", "skipped"):
        return {"status": "refused", "message": "status must be in_progress, done or skipped"}
    if status == "skipped" and not note:
        return {"status": "refused", "message": "a skipped item needs a reason in note"}
    state.checklist[item - 1].update(status=status, note=note)
    done = sum(c["status"] in ("done", "skipped") for c in state.checklist)
    return {"status": "ok", "message": f"{done}/{len(state.checklist)} done", "data": state.checklist}


def record_finding(state, args: dict) -> dict:
    text = (args.get("text") or "").strip()
    ids = [str(e) for e in (args.get("evidence_ids") or [])]
    claim = args.get("claim")
    problems = []
    if not text or len(text) > TEXT_LIMIT:
        problems.append(f"text must be 1-{TEXT_LIMIT} characters")
    if not ids:
        problems.append("cite at least one evidence id")
    if claim not in CLAIMS:
        problems.append(f"claim must be one of {sorted(CLAIMS)}")
    problems += evidence_problems(state, ids)
    problems += [f"placeholder {{{{{x}}}}} does not resolve" for x in unresolved_placeholders(state, text)]
    if problems:
        return {"status": "refused", "message": "finding refused", "problems": problems}
    fid = state.next_id("f", state.findings)
    state.findings[fid] = {"id": fid, "text": text, "evidence_ids": ids, "claim": claim}
    return {"status": "ok", "message": f"recorded {fid}", "finding_id": fid}


def _text_items(args: dict) -> list[tuple[str, str]]:
    out = []
    for sec in ("background", "hypotheses", "caveats", "gaps"):
        out += [(f"{sec}[{i}]", t) for i, t in enumerate(args.get(sec) or []) if isinstance(t, str)]
    out += [(f"follow_ups[{i}]", fu.get("rationale", "")) for i, fu in enumerate(args.get("follow_ups") or [])
            if isinstance(fu, dict)]
    return out


def report_problems(state, args: dict) -> list[str]:
    fids = args.get("finding_ids") or []
    problems = [] if fids else ["cite at least one finding id"]
    for fid in fids:
        if fid not in state.findings:
            problems.append(f"finding {fid!r} does not exist")
        else:
            problems += finding_problems(state, fid, state.findings[fid])
    for where, text in _text_items(args):
        problems += [f"{where}: placeholder {{{{{x}}}}} does not resolve" for x in unresolved_placeholders(state, text)]
        nums = bare_numbers(state, text)
        if nums:
            problems.append(f"{where}: bare numbers {nums} - use placeholders")
        if where.startswith(("hypotheses", "background")) and _unnegated_support_words(text):
            problems.append(f"{where}: {where.split('[')[0]} cannot claim statistical support")
    # Anti-cherry-picking (SPEC §4): supported findings need a robustness verdict before submission.
    if state.plan.window_b is not None:
        for fid in fids:
            f = state.findings.get(fid)
            if f and f["claim"] == "supported":
                for _, pair, res in _cited_pairs(state, f["evidence_ids"]):
                    if res["pairs"][pair].get("supported") and robustness(state, pair)["verdict"] == "not checked":
                        problems.append(f"{fid}: run cutoff_sensitivity for {pair} before submitting")
    return problems


def submit_report(state, args: dict) -> dict:
    problems = report_problems(state, args)
    if problems:
        return {"status": "refused", "message": "report refused; fix these and resubmit", "problems": problems}
    state.report = build_report(state, args)
    return {"status": "submitted", "message": "report accepted", "data": state.report}


# ---------- report ----------

def build_report(state, args: dict) -> dict:
    findings = []
    for fid in args["finding_ids"]:
        f = state.findings[fid]
        text, segments = render(state, f["text"])
        pairs = sorted({pair for _, pair, _ in _cited_pairs(state, f["evidence_ids"])})
        rob = [{"pair": p, **robustness(state, p)} for p in pairs] if f["claim"] == "supported" else []
        badge, label = CLAIMS[f["claim"]]
        findings.append({**f, "rendered": text, "segments": segments, "badge": badge, "badge_label": label,
                         "pairs": pairs, "robustness": rob})
    stat_rows = []
    for fid in args["finding_ids"]:
        for pid, pair, res in _cited_pairs(state, state.findings[fid]["evidence_ids"]):
            if res["tool"] == "compare_intervals":
                v = res["values"]
                tl = dataset_config(state.plan.dataset)["time_label"]
                stat_rows.append({
                    "finding": fid, "pair": pair, "pid": pid,
                    **{k: format_value(v[f"{pair}.{k}"]["v"], v[f"{pair}.{k}"]["kind"], tl)
                       for k in ("delta", "ci", "q", "n_blocks_a", "n_blocks_b", "tau_ns_a", "tau_ns_b")},
                    "supported": res["pairs"][pair]["supported"], "provisional": res["pairs"][pair]["provisional"],
                    "robustness": robustness(state, pair)["label"]})
    caveats = STANDARD_CAVEATS + ([] if dataset_config(state.plan.dataset)["stats_valid"] else [UNTESTED_CAVEAT])

    def section(key):
        return [dict(zip(("text", "segments"), render(state, t))) for t in (args.get(key) or [])]
    return {
        "findings": findings,
        "statistical_support": stat_rows,
        "background": section("background"),
        "background_label": BACKGROUND_LABEL,
        "hypotheses": section("hypotheses"),
        "caveats": [{"text": c, "segments": [{"text": c}]} for c in caveats] + section("caveats"),
        "gaps": section("gaps"),
        "follow_ups": [fu for fu in (args.get("follow_ups") or []) if isinstance(fu, dict)],
    }
