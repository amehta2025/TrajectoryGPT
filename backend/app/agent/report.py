"""Findings, placeholders, the deterministic check, and the report (SPEC §4, §9 steps 6-7).

The model never writes numbers: it writes {{r2.HSE134-ASP118.delta}}, and code fills the value from the
stored tool result. The check runs inside the loop: record_finding verifies evidence ids and
placeholders; submit_report runs the full check and refuses with the specific problems.
"""
from __future__ import annotations

import re

from app.config import dataset_config

# {{r2.HSE134-ASP118.delta}}; "|pct" writes a frequency as a percentage, a change in percentage points.
PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z0-9_.\-]+)\s*(?:\|\s*(pct)\s*)?\}\}")
NUMBER = re.compile(r"(?<![A-Za-z0-9_.])[-+−]?\d+(?:\.\d+)?(?![0-9])")
# A number is allowed right after these words (residue numbers, frames, ranges like "residues 122-159").
ALLOWED_CONTEXT = re.compile(r"(?:\bresidues?|\bresids?|\bframes?|\bpath steps?)\s*(?:\d+\s*(?:[-–—]|to|and)\s*)?$", re.I)
SUPPORT_WORDS = re.compile(r"\b(supported|significant(?:ly)?|robust(?:ly)?)\b", re.I)
NEGATION = re.compile(r"(?:\bnot|\bno|\bun|n't|\bnor|\bwithout)\s*(?:\w+\s+){0,2}$", re.I)
TEXT_LIMIT = 1200
PLAIN_LIMIT = 400

# claim -> (badge, label shown next to the finding)
CLAIMS = {
    "supported": ("supported", "supported"),
    "not_supported": ("unsupported", "not supported — consistent with noise"),
    "null": ("null", "no supported change found"),
    "descriptive": ("descriptive", "descriptive — not statistically tested"),
}
BACKGROUND_LABEL = "background — not computed"
INTERPRETATION_LABEL = "interpretation — not computed"
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
    if kind == "sci":
        return f"{v:.1e}".replace("-", "−")
    if kind == "match":
        return "matches" if v else "does not match"
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
    if kind == "setting_distance":
        return f"{v:g} Å"
    if kind == "ns":
        return f"{v:g} ns"
    return str(v)


# ---------- approved plan settings ----------
# Settings the user approved (cutoff, windows, sequence exclusion, residue ranges...) are not computed results:
# the agent may cite them as {{plan.<name>}}, and a bare number equal to one of them is not flagged.

def plan_values(plan) -> dict[str, dict]:
    """{name: {"v", "kind"}} for {{plan.<name>}} placeholders."""
    dt = dataset_config(plan.dataset)["dt_ns"]
    out = {"cutoff": {"v": float(plan.cutoff), "kind": "setting_distance"},
           "robustness_cutoffs": {"v": list(plan.robustness_cutoffs), "kind": "cutoff_list"},
           "sequence_exclusion": {"v": int(plan.sequence_exclusion), "kind": "count"},
           "stride": {"v": int(plan.stride), "kind": "count"}}
    for w in ("a", "b"):
        win = getattr(plan, f"window_{w}")
        if not win:
            continue
        out[f"window_{w}_first_frame"] = {"v": int(win[0]), "kind": "frame"}
        out[f"window_{w}_last_frame"] = {"v": int(win[1]) - 1, "kind": "frame"}
        out[f"window_{w}_stop_frame"] = {"v": int(win[1]), "kind": "frame"}  # as the plan shows it, [start, stop)
        if dt:
            out[f"window_{w}_start_ns"] = {"v": round(win[0] * dt), "kind": "ns"}
            out[f"window_{w}_end_ns"] = {"v": round(win[1] * dt), "kind": "ns"}
            out[f"window_{w}_length_ns"] = {"v": round((win[1] - win[0]) * dt), "kind": "ns"}
    if any(s.get("tool") in ("native_contacts", "reproduction_check") for s in plan.steps):
        out["native_radius"] = {"v": float(plan.native_radius), "kind": "setting_distance"}
    for g, (lo, hi) in (plan.residue_ranges or {}).items():
        out[f"range_{g}_first"] = {"v": int(lo), "kind": "count"}
        out[f"range_{g}_last"] = {"v": int(hi), "kind": "count"}
    return out


def plan_numbers(plan) -> set[float]:
    """Every number an approved setting shows, for the bare-number check (exact matches only)."""
    nums = set()
    for val in plan_values(plan).values():
        for x in (val["v"] if isinstance(val["v"], list) else [val["v"]]):
            nums.add(round(float(x), 3))
    return nums


def bare_hint(state) -> str:
    """What to write instead of a bare number, for refusal messages."""
    names = ", ".join(f"{{{{plan.{k}}}}}" for k in plan_values(state.plan))
    return f"write a computed value as its result placeholder (e.g. {{{{r1.n_supported}}}}) and a plan setting as one of {names}"


def format_pct(v: float, kind: str) -> str | None:
    """Plain-language percentages: a frequency as "53%", a change as "13 percentage points" (no sign:
    the sentence says the direction). None for other kinds."""
    if kind == "freq":
        if 0 < v < 0.001:
            return "less than 0.1%"
        if 0.995 < v < 1:
            return "more than 99%"
        return f"{v * 100:.1f}%" if v < 0.1 else f"{v * 100:.0f}%"
    if kind == "delta":
        pts = abs(v) * 100
        return "less than 1 percentage point" if 0 < pts < 0.5 else f"{pts:.0f} percentage point{'' if round(pts) == 1 else 's'}"
    return None


def resolve(state, path: str, fmt: str | None = None) -> dict | None:
    """'r2.HSE134-ASP118.delta' -> {"v", "kind", "pid", "text"}; None if it does not resolve.
    'plan.cutoff' and the other approved plan settings resolve too (plan_values)."""
    pid, _, name = path.partition(".")
    if pid == "plan":
        val = plan_values(state.plan).get(name)
        if val is None or fmt == "pct":
            return None
        return {**val, "pid": "plan", "path": path, "text": format_value(val["v"], val["kind"])}
    res = state.results.get(pid)
    if res is None or res["status"] != "ok" or name not in res["values"]:
        return None
    val = res["values"][name]
    if fmt == "pct":
        text = None if val["v"] is None else format_pct(val["v"], val["kind"])
        return None if text is None else {**val, "pid": pid, "path": path, "text": text}
    tl = dataset_config(state.plan.dataset)["time_label"]
    return {**val, "pid": pid, "path": path, "text": format_value(val["v"], val["kind"], tl)}


def render(state, text: str) -> tuple[str, list[dict]]:
    """Fill placeholders; returns (text, segments). Segments alternate plain text {"text"} and filled
    values {"text", "pid", "path", "kind", "v"} so the UI can show each value's provenance on hover."""
    segments, pos = [], 0
    for m in PLACEHOLDER.finditer(text):
        if m.start() > pos:
            segments.append({"text": text[pos:m.start()]})
        r = resolve(state, m[1], m[2])
        segments.append(r if r is not None else {"text": m[0]})
        pos = m.end()
    if pos < len(text):
        segments.append({"text": text[pos:]})
    return "".join(s["text"] for s in segments), segments


# ---------- individual checks ----------

def unresolved_placeholders(state, text: str) -> list[str]:
    return [m[1] + (f"|{m[2]}" if m[2] else "") for m in PLACEHOLDER.finditer(text) if resolve(state, m[1], m[2]) is None]


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


# Counts spelled out ("four contacts") are numbers too. "one" is left out ("one simulation", "one of"), and so is
# a number naming the setup rather than a result ("the two domains", "the two halves").
NUMBER_WORDS = re.compile(r"\b(two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|"
                          r"sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|"
                          r"hundred|thousand|dozen|dozens|hundreds|thousands)\b"
                          r"(?!\s+(?:moving\s+)?(?:parts|domains|halves|windows|groups|regions|lobes|states|sides)\b)", re.I)


def number_words(text: str) -> list[str]:
    """Spelled-out numbers left in plain text after removing placeholders."""
    return NUMBER_WORDS.findall(PLACEHOLDER.sub(" ", text))


def bare_numbers(state, text: str) -> list[str]:
    """Numerals left after removing placeholders, except residue/frame numbers and numbers exactly equal to an
    approved plan setting (cutoffs, window bounds and lengths in ns, sequence exclusion, residue ranges, the
    native-contacts radius...). Computed results still need placeholders."""
    plan = state.plan
    cutoffs = plan_numbers(plan)
    stripped = PLACEHOLDER.sub(" ", text)
    bad = []
    for m in NUMBER.finditer(stripped):
        before, after = stripped[:m.start()], stripped[m.end():]
        if ALLOWED_CONTEXT.search(before[-40:]):
            continue
        tok = m[0].replace("−", "-")
        if round(float(tok), 3) in cutoffs:
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


# Causal or linking wording (red-team A11): findings and the headline say what was measured; an explanation
# of why belongs in the labelled interpretation or the hypotheses, which this check does not cover.
CAUSAL = re.compile(r"\b(caus(?:e|es|ed|ing)|because of|due to|driv(?:e|es|en|ing)|drove|lead(?:s)? to|led to|"
                    r"result(?:s|ed)? in|responsible for|trigger(?:s|ed)?|explain(?:s|ed)?|underl(?:ie|ies|ying)|"
                    r"link(?:s|ed)? (?:to|with)|associated with|correlat(?:es|ed) with|mechanism)\b", re.I)


def causal_words(text: str) -> list[str]:
    return [m.group(0) for m in CAUSAL.finditer(PLACEHOLDER.sub(" ", text))]


def finding_problems(state, fid: str, f: dict) -> list[str]:
    """Full deterministic check for one finding (SPEC §9 step 7)."""
    p = [f"{fid}: {x}" for x in evidence_problems(state, f["evidence_ids"])]
    p += [f"{fid}: placeholder {{{{{x}}}}} does not resolve" for x in unresolved_placeholders(state, f["text"])]
    nums = bare_numbers(state, f["text"])
    if nums:
        p.append(f"{fid}: replace the bare numbers {nums}: {bare_hint(state)}")
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
    if causal_words(f["text"]):
        p.append(f"{fid}: causal or linking wording {causal_words(f['text'])} - state what was measured; put possible "
                 f"explanations in the interpretation or hypotheses")
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
    ids = [str(e) for e in (args.get("evidence_ids") or [])]
    text = autorepair(state, (args.get("text") or "").strip(), ids)
    claim = args.get("claim")
    if claim is None and "claim" in args:
        claim = "null"  # Muse Spark Open sends JSON null for the claim type named "null" (red-team G2)
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
    out = {"status": "ok", "message": f"recorded {fid}", "finding_id": fid}
    plain = autorepair(state, (args.get("plain") or "").strip(), ids)
    if plain:
        bad = plain_problems(state, state.findings[fid], plain)
        if bad:  # the finding stands; the UI falls back to its code-built sentence
            out["plain_dropped"] = bad
        else:
            state.findings[fid]["plain"] = plain
    return out


def plain_problems(state, f: dict, plain: str) -> list[str]:
    """Checks on a finding's plain sentence: the same as on its text (placeholders, no bare numbers, wording
    that matches the claim), reporting only what the plain sentence adds."""
    if len(plain) > PLAIN_LIMIT:
        return [f"plain must be at most {PLAIN_LIMIT} characters"]
    own = set(finding_problems(state, f["id"], f))
    out = [p.replace(f"{f['id']}: ", "plain: ", 1) for p in finding_problems(state, f["id"], {**f, "text": plain})
           if p not in own]
    if number_words(plain):
        out.append(f"plain: numbers written as words {number_words(plain)} - use placeholders")
    return out


def headline_problems(state, args: dict) -> list[str]:
    """The headline answers the question in plain words. It may say supported / robust only when a cited
    finding is supported (and, for robust, a cited pair is robust); every number is a placeholder."""
    text = (args.get("headline") or "").strip()
    if not text:
        return []
    if len(text) > PLAIN_LIMIT:
        return [f"headline must be at most {PLAIN_LIMIT} characters"]
    p = [f"headline: placeholder {{{{{x}}}}} does not resolve" for x in unresolved_placeholders(state, text)]
    nums = bare_numbers(state, text) + number_words(text)
    if nums:
        p.append(f"headline: bare numbers {nums} - use placeholders such as {{{{r1.n_supported}}}}")
    if causal_words(text):
        p.append(f"headline: causal or linking wording {causal_words(text)} - answer with what was measured")
    findings = [state.findings[f] for f in args.get("finding_ids") or [] if f in state.findings]
    supported = [f for f in findings if f["claim"] == "supported"]
    words = [w.lower() for w in _unnegated_support_words(text)]
    if any(not w.startswith("robust") for w in words) and not supported:
        p.append("headline: says supported/significant but no cited finding is supported")
    robust = any(robustness(state, pair)["verdict"] == "robust"
                 for f in supported for _, pair, _ in _cited_pairs(state, f["evidence_ids"]))
    if any(w.startswith("robust") for w in words) and not robust:
        p.append("headline: says robust but no supported pair has a robust cutoff_sensitivity verdict")
    return p


MAX_NEXT_QUESTIONS = 2


def next_questions(state, args: dict) -> tuple[list[str], list[str]]:
    """Muse's suggested next questions (buttons in the UI): at most two, each a short question with no
    placeholders and no numbers beyond residue numbers and approved cutoffs. Returns (kept, problems)."""
    kept, problems = [], []
    for i, q in enumerate((args.get("next_questions") or [])[:MAX_NEXT_QUESTIONS]):
        q = q.strip() if isinstance(q, str) else ""
        why = ("must be 10-200 characters" if not 10 <= len(q) <= 200 else "must end with '?'" if not q.endswith("?")
               else "cannot contain placeholders" if PLACEHOLDER.search(q)
               else f"bare numbers {bare_numbers(state, q) + number_words(q)}"
               if bare_numbers(state, q) or number_words(q) else None)
        if why:
            problems.append(f"next_questions[{i}]: {why}")
        else:
            kept.append(q)
    return kept, problems


def interpretation_problems(state, args: dict) -> list[str]:
    """Interpretation is labelled as such and, like a hypothesis, claims no statistical support and writes no numbers."""
    text = (args.get("interpretation") or "").strip()
    if not text:
        return []
    p = [] if len(text) <= PLAIN_LIMIT else [f"interpretation must be at most {PLAIN_LIMIT} characters"]
    p += [f"interpretation: placeholder {{{{{x}}}}} does not resolve" for x in unresolved_placeholders(state, text)]
    nums = bare_numbers(state, text) + number_words(text)
    if nums:
        p.append(f"interpretation: replace the bare numbers {nums}: {bare_hint(state)}")
    if _unnegated_support_words(text):
        p.append("interpretation cannot claim statistical support")
    return p


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
            problems.append(f"{where}: replace the bare numbers {nums}: {bare_hint(state)}")
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
    args = repair_report_args(state, args)
    problems = report_problems(state, args)
    if problems:
        return {"status": "refused", "message": "report refused; fix these and resubmit", "problems": problems}
    state.report = build_report(state, args)
    out = {"status": "submitted", "message": "report accepted", "data": state.report}
    if state.report["text_dropped"]:  # Muse's headline / interpretation failed a check: code templates are shown
        out["text_dropped"] = state.report["text_dropped"]
    return out


# ---------- report ----------

def build_report(state, args: dict) -> dict:
    findings = []
    for fid in args["finding_ids"]:
        f = state.findings[fid]
        text, segments = render(state, f["text"])
        pairs = sorted({pair for _, pair, _ in _cited_pairs(state, f["evidence_ids"])})
        rob = [{"pair": p, **robustness(state, p)} for p in pairs] if f["claim"] == "supported" else []
        badge, label = CLAIMS[f["claim"]]
        plain = dict(zip(("text", "segments"), render(state, f["plain"]))) if f.get("plain") else None
        findings.append({**f, "rendered": text, "segments": segments, "badge": badge, "badge_label": label,
                         "pairs": pairs, "robustness": rob, "plain": plain})
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
    # The sampling caveat is about statistics; on a dataset without them, say that no statistics were computed.
    caveats = STANDARD_CAVEATS if dataset_config(state.plan.dataset)["stats_valid"] else [STANDARD_CAVEATS[0], UNTESTED_CAVEAT]

    def section(key):
        return [dict(zip(("text", "segments"), render(state, t))) for t in (args.get(key) or [])]

    def written(key, problems):
        text = (args.get(key) or "").strip()
        return dict(zip(("text", "segments"), render(state, text))) if text and not problems else None
    hp, ip = headline_problems(state, args), interpretation_problems(state, args)
    nq, nqp = next_questions(state, args)
    return {
        "next_questions": nq,
        "headline": written("headline", hp),
        "interpretation": written("interpretation", ip),
        "interpretation_label": INTERPRETATION_LABEL,
        "text_dropped": hp + ip + nqp,
        "findings": findings,
        "statistical_support": stat_rows,
        "background": section("background"),
        "background_label": BACKGROUND_LABEL,
        "hypotheses": section("hypotheses"),
        "caveats": [{"text": c, "segments": [{"text": c}]} for c in list(state.plan.notes) + caveats] + section("caveats"),
        "gaps": section("gaps"),
        "follow_ups": [fu for fu in (args.get("follow_ups") or []) if isinstance(fu, dict)],
    }


# ---------- auto-repair: numbers the agent typed that code can stand behind ----------
# A number that exactly equals an approved plan setting, or a value of a result the text cites, is replaced by
# its placeholder, so code fills it (with provenance) and the check passes. A number matching nothing is left
# for the check to refuse: only code may write computed numbers.

NUMERIC_KINDS = {"freq", "delta", "q", "sci", "count", "frame", "distance", "tau_ns", "chunks", "setting_distance", "ns"}


def _candidates(state, pids: list[str]) -> list[tuple[str, dict]]:
    """(placeholder path, value) to match against: plan settings, then the cited pairs' values, then the cited
    results' other values."""
    out = [(f"plan.{k}", v) for k, v in plan_values(state.plan).items()]
    pair_first, rest = [], []
    wanted = {e.partition(".")[0]: set() for e in pids}
    for e in pids:
        pid, _, pair = e.partition(".")
        if pair:
            wanted[pid].add(pair)
    for pid, pairs in wanted.items():
        res = state.results.get(pid)
        if not res or res["status"] != "ok":
            continue
        for name, val in res["values"].items():
            (pair_first if name.split(".")[0] in pairs else rest).append((f"{pid}.{name}", val))
    return out + pair_first + rest


def _matches(tok: str, val: dict) -> bool:
    if val["kind"] not in NUMERIC_KINDS or isinstance(val["v"], (list, bool)) or val["v"] is None:
        return False
    # Exact only: the value as the tool summary showed it (3 decimals) or as the report prints it. A shorter
    # rounding ("0.5" for 0.527) is not a match.
    try:
        t = float(tok.replace("−", "-").lstrip("+"))
        v = float(val["v"])
    except (TypeError, ValueError):
        return False
    m = re.search(r"[-+]?\d+(?:\.\d+)?(?:e[-+]?\d+)?", format_value(v, val["kind"]).replace("−", "-"))
    return t == round(v, 3) or (m is not None and t == float(m[0]))


def autorepair(state, text: str, pids: list[str]) -> str:
    """Replace each bare number (one that bare_numbers would flag) that equals a plan setting or a cited value
    with that value's placeholder; '53%' becomes a |pct placeholder of a matching frequency."""
    if not text:
        return text
    bad = set(bare_numbers(state, text))
    if not bad:
        return text
    cands = _candidates(state, pids)
    out, pos = [], 0
    for m in NUMBER.finditer(text):
        tok = m[0]
        if tok not in bad or any(m.start() >= a and m.end() <= b for a, b in _placeholder_spans(text)):
            continue
        after = text[m.end():m.end() + 1]
        repl, end = None, m.end()
        if after == "%":
            for path, val in cands:
                if val["kind"] == "freq" and val["v"] is not None and format_pct(val["v"], "freq") == f"{tok}%":
                    repl, end = f"{{{{{path}|pct}}}}", m.end() + 1
                    break
        if repl is None:
            for path, val in cands:
                if _matches(tok, val):
                    repl = f"{{{{{path}}}}}"
                    break
        if repl is None:
            continue
        out.append(text[pos:m.start()] + repl)
        pos = end
    return "".join(out) + text[pos:]


def _placeholder_spans(text: str) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for m in PLACEHOLDER.finditer(text)]


def repair_report_args(state, args: dict) -> dict:
    """submit_report's text items with auto-repaired numbers; values come from the results its findings cite."""
    pids = [e for fid in args.get("finding_ids") or [] for e in (state.findings.get(fid) or {}).get("evidence_ids", [])]
    pids = pids or list(state.results)
    fix = lambda t: autorepair(state, t, pids) if isinstance(t, str) else t
    out = dict(args)
    for key in ("headline", "interpretation"):
        if isinstance(out.get(key), str):
            out[key] = fix(out[key])
    for key in ("background", "hypotheses", "caveats", "gaps", "next_questions"):
        if isinstance(out.get(key), list):
            out[key] = [fix(t) for t in out[key]]
    if isinstance(out.get("follow_ups"), list):
        out["follow_ups"] = [{**fu, "rationale": fix(fu.get("rationale", ""))} if isinstance(fu, dict) else fu
                             for fu in out["follow_ups"]]
    return out


# ---------- fallback: the report from computed results when the AI's wording keeps failing the checks ----------

FALLBACK_NOTE = ("Some of the AI's wording couldn't be verified, so it was removed. "
                 "Every result shown comes from computation.")


def computed_findings(state) -> list[str]:
    """Findings written by code from the stored results (no model text), one or a few per analysis."""
    made = []

    def add(text, ids, claim):
        fid = state.next_id("f", state.findings)
        state.findings[fid] = {"id": fid, "text": text, "evidence_ids": ids, "claim": claim, "code_built": True}
        made.append(fid)

    for pid, res in state.results.items():
        if res["status"] != "ok":
            continue
        tool, pairs, v = res["tool"], res.get("pairs") or {}, res["values"]
        if tool == "compare_intervals":
            sup = sorted((p for p, d in pairs.items() if d.get("supported")), key=lambda p: v.get(f"{p}.q", {}).get("v", 1))
            for p in sup[:3]:
                add(f"{p} contact frequency changed from {{{{{pid}.{p}.freq_a}}}} to {{{{{pid}.{p}.freq_b}}}} "
                    f"(change {{{{{pid}.{p}.delta}}}}, CI {{{{{pid}.{p}.ci}}}}, q {{{{{pid}.{p}.q}}}}).", [f"{pid}.{p}"], "supported")
            if not sup:
                add(f"None of the {{{{{pid}.n_tested}}}} tested pairs changed beyond random variation.", [pid], "null")
        elif tool == "calculate_contacts" and "any_contact_freq" in v:
            add(f"The two groups touch in {{{{{pid}.any_contact_freq|pct}}}} of frames, across "
                f"{{{{{pid}.n_pairs_in_contact}}}} residue pairs.", [pid], "descriptive")
        elif tool == "distance_over_time" and pairs:
            p = next(iter(pairs))
            add(f"{p}: median minimum distance {{{{{pid}.{p}.median_distance}}}}, within the cutoff in "
                f"{{{{{pid}.{p}.frac_in_contact|pct}}}} of frames.", [f"{pid}.{p}"], "descriptive")
        elif tool == "native_contacts" and "ref0.q_first" in v:
            add(f"The fraction of reference contacts went from {{{{{pid}.ref0.q_first}}}} to {{{{{pid}.ref0.q_last}}}}.",
                [pid], "descriptive")
        elif tool == "reproduction_check":
            add(f"Compared with the published code, our values {{{{{pid}.match}}}} (largest difference "
                f"{{{{{pid}.max_abs_diff}}}}).", [pid], "descriptive")
        elif tool == "residue_flexibility":
            meds = [k for k in v if k.startswith("median_rmsf.")]
            if meds:
                add("Median flexibility by domain: " + ", ".join(f"{k.split('.', 1)[1]} {{{{{pid}.{k}}}}}" for k in meds) + ".",
                    [pid], "descriptive")
        elif tool == "domain_opening":
            name = next((k.rsplit(".", 1)[0] for k in v if k.endswith(".median_distance")), None)
            if name:
                add(f"{name} centre-of-mass distance: median {{{{{pid}.{name}.median_distance}}}}, from "
                    f"{{{{{pid}.{name}.min_distance}}}} to {{{{{pid}.{name}.max_distance}}}}.", [pid], "descriptive")
    return made


def fallback_report(state, args: dict | None, problems: list[str]) -> dict | None:
    """The report when the agent's wording failed the checks after its retries: keep the findings and text that
    pass, drop the rest, and use code-built findings when none pass. None when there is no computed result to
    report (then the run stops for review)."""
    args = repair_report_args(state, dict(args or {}))
    keep = [fid for fid in (args.get("finding_ids") or list(state.findings))
            if fid in state.findings and not finding_problems(state, fid, state.findings[fid])]
    if not keep:
        keep = computed_findings(state)
    if not keep:
        return None
    clean = {k: v for k, v in args.items() if k in ("headline", "interpretation", "next_questions")}
    for sec in ("background", "hypotheses", "caveats", "gaps"):
        clean[sec] = [t for t in args.get(sec) or [] if isinstance(t, str)
                      and not bare_numbers(state, t) and not unresolved_placeholders(state, t)
                      and not (sec in ("hypotheses", "background") and _unnegated_support_words(t))]
    clean["follow_ups"] = [fu for fu in args.get("follow_ups") or [] if isinstance(fu, dict)
                           and not bare_numbers(state, fu.get("rationale", "")) and not unresolved_placeholders(state, fu.get("rationale", ""))]
    clean["finding_ids"] = keep
    rep = build_report(state, clean)
    rep["fallback"] = {"note": FALLBACK_NOTE, "removed": list(problems)}
    return rep
