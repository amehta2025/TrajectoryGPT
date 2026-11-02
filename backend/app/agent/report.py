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
    return str(v)


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
    """'r2.HSE134-ASP118.delta' -> {"v", "kind", "pid", "text"}; None if it does not resolve."""
    pid, _, name = path.partition(".")
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
    text = (args.get("text") or "").strip()
    ids = [str(e) for e in (args.get("evidence_ids") or [])]
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
    plain = (args.get("plain") or "").strip()
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
        p.append(f"interpretation: bare numbers {nums} - use placeholders")
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
