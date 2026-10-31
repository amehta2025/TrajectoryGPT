"""Figure-mode extraction accuracy on a small test set of CC-BY figures (assets/figures/testset/).

Each figure is sent the way the app sends it (image + the caption text a user would paste, capped at 2,000
characters, like /figure/plan) through the production extract_spec, twice. Every field is scored against a
hand-written ground truth:
  exact   - the extracted value states the truth
  close   - right in substance but incomplete, or right value with the wrong confidence
  wrong   - a different value stated as fact (or a value invented for something the text does not state)
  missing - no field for it, or "unknown" where the text does state it
For a setting the given text does NOT state, "exact" means the model said so (unknown / not stated / low
confidence); asserting a specific value with high confidence is "wrong".
The auto-scores are a first pass; overrides after reading the raw specs are recorded in OVERRIDES.

Run (repo root, PowerShell or Git Bash):  .venv/Scripts/python backend/scripts/figure_eval.py
Writes notes/figure_eval/specs.json (raw specs) and notes/figure_eval/scores.json.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.agent.figure import extract_spec  # noqa: E402
from app.agent.llm import LLM  # noqa: E402

FIG = ROOT / "assets" / "figures"
OUT = ROOT / "notes" / "figure_eval"
RUNS = 2
NOTE_LIMIT = 2000  # /figure/plan's cap when the scores in notes/recon.md were made (now figure.NOTE_LIMIT, 8,000)
UNKNOWN = r"unknown|not (stated|given|specified|shown|reported|provided)|unspecified|unclear|n/?a\b|none given"



# Each field: (name keywords to find it, ground truth text, exact regexes (all), close regexes (any), not_stated).
def gt(**fields):
    return fields


TESTSET = [
    {"id": "iyer2020_fig5c", "image": FIG / "iyer2020_fig5c_adk_difference_contact_map.png", "text": FIG / "iyer2020_fig5c_caption.txt",
     "kind": "difference_contact_map", "mode": "transfer", "dataset": "none",
     "fields": gt(
         protein=(["protein", "system"], "E. coli adenylate kinase", [r"adenylate kinase|\badk\b"], [], False),
         data_source=(["data source", "source", "structure"], "PDB 4jzkB (closed) vs 4akeA (open)", [r"4jzk", r"4ake"], [r"4jzk|4ake|closed.*open"], False),
         atoms=(["atom selection", "atoms", "selection"], "side-chain heavy atoms", [r"side.?chain", r"heavy"], [r"side.?chain|heavy"], False),
         cutoff=(["cutoff", "radius", "distance"], "5 Å", [r"\b5\s*(Å|A\b|angstrom)"], [r"\b5\b"], False),
         axes=(["x axis", "x-axis", "axis"], "alignment position", [r"alignment"], [r"position|residue"], False),
         contact=(["contact definition", "contact"], "any side-chain heavy-atom pair < 5 Å; red closed-only, blue open-only",
                  [r"side.?chain|heavy", r"\b5\b"], [r"red|blue|closed|open"], False),
     )},
    {"id": "userguide_q1q2", "image": FIG / "userguide_q1q2_replot.png", "text": None,
     "kind": "native_contacts", "mode": "exact", "dataset": "adk_transition",
     "fields": gt(
         protein=(["protein", "system"], "adenylate kinase", [r"adenylate kinase|\badk\b"], [], False),
         data_source=(["data source", "source", "data"], "DIMS closed-to-open path (MDAnalysisTests)", [r"dims|transition|mdanalysis"], [r"trajectory|path"], False),
         atoms=(["atom selection", "atoms", "selection"], "C-alpha (name CA)", [r"\bca\b|c.?alpha|cα"], [], False),
         cutoff=(["cutoff", "radius"], "8 Å radius", [r"\b8\b"], [], False),
         reference=(["reference"], "Q1: first frame (closed); Q2: last frame (open)", [r"first|closed|frame 0", r"last|open"], [r"first|last|closed|open"], False),
         axes=(["x axis", "x-axis", "axis"], "frame; Q1 vs Q2", [r"frame"], [r"q1|time|step"], False),
     )},
    {"id": "milano2017_fig5", "image": FIG / "testset" / "milano2017_fig5_mindist.png", "text": FIG / "testset" / "milano2017_fig5_mindist.txt",
     "kind": "distance_trace", "mode": "transfer", "dataset": "none",
     "fields": gt(
         protein=(["protein", "system"], "GabR (holo vs apo)", [r"gabr"], [r"transcription|regulator"], False),
         pairs=(["pair", "residue", "residues"], "Ile104B–Leu340A (image; caption says Ile140B) and Leu99B–Pro326A",
                [r"leu99", r"pro326", r"leu340"], [r"ile1[04][04]|leu340|leu99"], False),
         measure=(["analysis", "distance", "y axis", "measure"], "minimum distance (nm) vs time", [r"minimum|min\b"], [r"distance"], False),
         atoms=(["atom selection", "atoms", "selection"], "not stated", [], [], True),
         axes=(["x axis", "x-axis"], "time (ns), 0–350", [r"time", r"ns"], [r"time"], False),
         units=(["y axis", "y-axis", "unit"], "distance in nm", [r"\bnm\b|nanomet"], [r"distance"], False),
     )},
    {"id": "milano2017_fig2bc", "image": FIG / "testset" / "milano2017_fig2bc_rmsf.png", "text": FIG / "testset" / "milano2017_fig2bc_rmsf.txt",
     "kind": "rmsf", "mode": "transfer", "dataset": "none",
     "fields": gt(
         protein=(["protein", "system"], "GabR, chains A and B, holo vs apo", [r"gabr"], [r"chain|subunit"], False),
         atoms=(["atom selection", "atoms", "selection"], "main-chain atoms", [r"main.?chain|backbone"], [], False),
         fit=(["reference", "superpos", "fit", "align"], "superimposed on the initial structure", [r"initial|first|start"], [r"superim|fit|align"], False),
         axes=(["x axis", "x-axis"], "residue", [r"residue"], [], False),
         units=(["y axis", "y-axis", "unit"], "RMSF (nm)", [r"\bnm\b|nanomet"], [r"rmsf|fluct"], False),
     )},
    {"id": "milano2017_fig2a", "image": FIG / "testset" / "milano2017_fig2a_rmsd.png", "text": FIG / "testset" / "milano2017_fig2a_rmsd.txt",
     "kind": "other", "mode": "transfer", "dataset": "none", "fields": {}},
    {"id": "chen2011_fig7c", "image": FIG / "testset" / "chen2011_fig7c_distance.png", "text": FIG / "testset" / "chen2011_fig7c_distance.txt",
     "kind": "distance_trace", "mode": "transfer", "dataset": "none",
     "fields": gt(
         protein=(["protein", "system"], "integrin (caption: 'partially-extended integrin'; αVβ3 is not named)", [r"integrin"], [], False),
         pairs=(["pair", "residue", "residues", "interaction"], "Asp393–Arg633 salt bridge and a hybrid/EGF4 nonpolar group",
                [r"asp393", r"arg633"], [r"asp393|arg633|nonpolar"], False),
         atoms=(["atom selection", "atoms", "selection", "distance definition"], "not stated in the caption (paper: side-chain O/N centres of mass)", [], [], True),
         axes=(["x axis", "x-axis"], "simulation time (ns), free MD 1 and 2", [r"time", r"ns"], [r"time"], False),
         units=(["y axis", "y-axis", "unit"], "distance (Å)", [r"Å|angstrom|\bA\b"], [r"distance"], False),
         reference=(["reference"], "dashed lines: distances in the equilibrated bent structure", [r"bent|dashed"], [r"reference|equilibrat"], False),
     )},
    {"id": "habibi2016_fig7a", "image": FIG / "testset" / "habibi2016_fig7a_native_contacts.png", "text": FIG / "testset" / "habibi2016_fig7a_native_contacts.txt",
     "kind": "native_contacts", "mode": "transfer", "dataset": "none",
     "fields": gt(
         protein=(["protein", "system"], "not stated (paper: SOD1 monomer, PDB 4BCZ)", [], [r"sod1"], True),
         atoms=(["atom selection", "atoms", "selection"], "heavy atoms", [r"heavy"], [], False),
         cutoff=(["cutoff", "radius"], "0.6 nm", [r"0\.6\s*nm|6\s*(Å|A\b|angstrom)"], [r"0\.6|\b6\b"], False),
         separation=(["separation", "sequence", "exclusion", "contact definition"], "three or more residues apart", [r"three|\b3\b"], [r"residues apart|separat"], False),
         reference=(["reference"], "native state", [r"native"], [], False),
         axes=(["x axis", "x-axis"], "total Q; y = residue, colour = per-residue Qk", [r"\bq\b|fraction of native"], [r"native"], False),
     )},
    {"id": "perezhernandez2025_fig2b", "image": FIG / "testset" / "perezhernandez2025_fig2b_contact_frequency.png",
     "text": FIG / "testset" / "perezhernandez2025_fig2b_contact_frequency.txt",
     "kind": "contact_map", "mode": "transfer", "dataset": "none",
     "fields": gt(
         protein=(["protein", "system"], "G protein Gα (LEU393, LEU394) with β2-adrenergic receptor", [r"g.?protein|gα|galpha|gnas"], [r"receptor|β2|adrb2"], False),
         atoms=(["atom selection", "atoms", "selection", "scheme"], "closest heavy atoms", [r"heavy"], [r"closest"], False),
         cutoff=(["cutoff", "radius"], "4.5 Å", [r"4\.5"], [], False),
         exclusion=(["exclu", "neighbor", "neighbour", "separation"], "4 nearest bonded neighbours excluded", [r"\b4\b|four"], [r"exclu|neighbo"], False),
         axes=(["y axis", "y-axis"], "contact frequency", [r"frequenc"], [], False),
     )},
]

# Manual overrides after reading the raw specs: (figure id, run, field) -> (score, reason). Filled in by hand.
OVERRIDES: dict[tuple[str, int, str], tuple[str, str]] = {
    ("milano2017_fig5", 1, "pairs"): ("exact", "pairs given under 'atom selection'; flags Ile104B (image) vs Ile140B (caption)"),
    ("milano2017_fig5", 2, "pairs"): ("exact", "pairs given under 'atom selection'; flags Ile104B (image) vs Ile140B (caption)"),
    ("milano2017_fig5", 1, "atoms"): ("close", "'all inter-residue atom pairs' at medium confidence; the text does not say which atoms"),
    ("chen2011_fig7c", 1, "pairs"): ("exact", "Asp393-Arg633 and the nonpolar group given under 'atom selection'"),
    ("chen2011_fig7c", 2, "pairs"): ("exact", "Asp393-Arg633 and the nonpolar group given under 'atom selection'"),
    ("habibi2016_fig7a", 1, "protein"): ("exact", "names no protein; says 'not AdK, ~110-residue beta protein', which the axis supports"),
    ("habibi2016_fig7a", 1, "atoms"): ("close", "'atom selection' lists the four models; 'heavy atoms' appears in the cutoff field"),
    ("perezhernandez2025_fig2b", 1, "atoms"): ("close", "backbone/side-chain typing, no 'heavy'; the methods sentence was cut by the 2,000-character note limit"),
    ("perezhernandez2025_fig2b", 2, "atoms"): ("close", "'all heavy atoms', low confidence; the methods sentence was cut by the 2,000-character note limit"),
    ("perezhernandez2025_fig2b", 1, "exclusion"): ("exact", "'4 nearest bonded neighbors excluded' given under 'contact definition'"),
    ("perezhernandez2025_fig2b", 2, "exclusion"): ("exact", "'4 nearest bonded neighbors excluded' given under 'reference'"),
}


def find(spec_fields: list[dict], keys: list[str]) -> dict | None:
    for k in keys:
        for f in spec_fields:
            if k in f["name"].lower():
                return f
    return None


def score_field(f: dict | None, rule) -> str:
    _, truth, exact, close, not_stated = rule
    if f is None:
        return "exact" if not_stated else "missing"
    v = f"{f['value']}"
    says_unknown = bool(re.search(UNKNOWN, v, re.I))
    if not_stated:
        if says_unknown or f["confidence"] == "low":
            return "exact"
        return "close" if f["confidence"] == "medium" else "wrong"
    if says_unknown:
        return "missing"
    if exact and all(re.search(p, v, re.I) for p in exact):
        return "exact" if f["confidence"] != "low" else "close"
    if close and any(re.search(p, v, re.I) for p in close):
        return "close"
    return "wrong"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    llm = LLM()
    specs: dict[str, list[dict]] = {}
    for t in TESTSET:
        files = [(t["image"].name, "image/png", t["image"].read_bytes())]
        note = t["text"].read_text(encoding="utf-8")[:NOTE_LIMIT] if t["text"] else ""
        specs[t["id"]] = []
        for run in range(RUNS):
            spec, usages = extract_spec(llm, files, note)
            spec["_cost_usd"] = sum(u.cost_usd or 0 for u in usages)
            specs[t["id"]].append(spec)
            print(f"{t['id']} run {run + 1}: kind={spec['kind']} mode={spec['mode']} dataset={spec['dataset']} "
                  f"low={sum(f['confidence'] == 'low' for f in spec['fields'])}", flush=True)
    (OUT / "specs.json").write_text(json.dumps(specs, indent=1, ensure_ascii=False), encoding="utf-8")
    rescore(specs)


def rescore(specs: dict[str, list[dict]] | None = None) -> None:
    specs = specs or json.loads((OUT / "specs.json").read_text(encoding="utf-8"))
    scores = {}
    for t in TESTSET:
        rows = []
        for run, spec in enumerate(specs[t["id"]]):
            row = {"kind": "exact" if spec["kind"] == t["kind"] else "wrong",
                   "mode": "exact" if spec["mode"] == t["mode"] else "wrong",
                   "dataset": "exact" if spec["dataset"] == t["dataset"] else "wrong"}
            for name, rule in t["fields"].items():
                row[name] = score_field(find(spec["fields"], rule[0]), rule)
            for (fid, r, field), (s, _) in OVERRIDES.items():
                if fid == t["id"] and r == run + 1:
                    row[field] = s
            rows.append(row)
        scores[t["id"]] = rows
    (OUT / "scores.json").write_text(json.dumps(scores, indent=1), encoding="utf-8")
    for fid, rows in scores.items():
        for i, row in enumerate(rows):
            print(fid, i + 1, " ".join(f"{k}={v}" for k, v in row.items()))


if __name__ == "__main__":
    rescore() if "--rescore" in sys.argv else main()
