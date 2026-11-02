"""Data notes (red-team A6, A7): what a question asks for that the data cannot give, found by code.

"Compare the first 2 µs to the last 2 µs" on a 1 µs run, "residue 500" on a 214-residue protein, or "LYS30"
when residue 30 is SER. Each note is a plain sentence written here, with numbers filled by code (the model
never writes them). Notes go to the planner, the PlanCard, the agent, and the report's caveats.
"""
import re

from app.config import dataset_config
from app.science.loader import TRAJ_LOCK, load_universe

TIME = re.compile(r"(\d+(?:\.\d+)?)\s*(µs|μs|us|microseconds?|ns|nanoseconds?)(?![A-Za-z])", re.I)
RES_NAMED = re.compile(r"\b([A-Z]{3})\s?-?(\d{1,4})\b")
RES_NUMBER = re.compile(r"\bres(?:idue)?s?\s*#?\s*(\d{1,4})\b", re.I)
HIS = {"HIS", "HSD", "HSE", "HSP"}
AMINO = {"ALA", "ARG", "ASN", "ASP", "CYS", "GLN", "GLU", "GLY", "ILE", "LEU", "LYS", "MET", "PHE", "PRO", "SER",
         "THR", "TRP", "TYR", "VAL"} | HIS


def _ns(value: float, unit: str) -> float:
    return value if unit.lower().startswith("n") else value * 1000.0


def _time(ns: float) -> str:
    return f"{ns / 1000:.2f} µs" if ns >= 1000 else f"{ns:.0f} ns"


def data_notes(question: str, dataset: str) -> list[str]:
    cfg = dataset_config(dataset)
    with TRAJ_LOCK:
        u = load_universe(dataset)
        n_frames = u.trajectory.n_frames
        names = {int(r): str(n) for r, n in zip(u.residues.resids, u.residues.resnames)}
    first, last = min(names), max(names)
    notes: list[str] = []

    times = [_ns(float(v), unit) for v, unit in TIME.findall(question)]
    if times and cfg["dt_ns"] is None:
        notes.append("This dataset is a biased closed-to-open path with no physical time, so it is analysed in "
                     "path steps, not in ns or µs.")
    elif times:
        total = n_frames * cfg["dt_ns"]
        if max(times) > total * 1.01:
            notes.append(f"You asked about {_time(max(times))}, but this run covers only {_time(total)} "
                         f"({n_frames:,} frames), so the analysis can only use the run as it is.")

    for name, num in RES_NAMED.findall(question):
        n = int(num)
        if name not in AMINO:
            continue
        if n not in names:
            notes.append(f"There is no residue {n}: this protein has residues {first}–{last}.")
        elif name != names[n] and not (name in HIS and names[n] in HIS):
            notes.append(f"Residue {n} is {names[n]}, not {name}; the analysis uses {names[n]}{n}.")
    for num in RES_NUMBER.findall(question):
        if int(num) not in names:
            notes.append(f"There is no residue {int(num)}: this protein has residues {first}–{last}.")
    return list(dict.fromkeys(notes))
