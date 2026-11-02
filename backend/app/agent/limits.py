"""Data notes (red-team A6, A7): what a question asks for that the data cannot give, found by code.

"Compare the first 2 µs to the last 2 µs" on a 1 µs run, "residue 500" on a 214-residue protein, or "LYS30"
when residue 30 is SER. Each note is a plain sentence written here, with numbers filled by code (the model
never writes them). Notes go to the planner, the PlanCard, the agent, and the report's caveats.

A named residue that doesn't match the protein is not guessed at (red-team Round 2: "HSE143" was answered as
GLU143 when HSE134 was meant). residue_check() stops before planning and offers the likely residues; the
question is planned only once the user picks one.
"""
import re

from app.config import dataset_config
from app.science.loader import TRAJ_LOCK, load_universe

TIME = re.compile(r"(\d+(?:\.\d+)?)\s*(µs|μs|us|microseconds?|ns|nanoseconds?)(?![A-Za-z])", re.I)
# "ASP84", "ASP 84", "ASP-84"; lowercase or mixed case only when joined ("ser30", "Lys-30"), so "we met 3 times"
# is not read as MET3.
RES_NAMED = re.compile(r"\b(?:([A-Z]{3})\s?-?|([A-Za-z]{3})-?)(\d{1,4})\b")
RES_NUMBER = re.compile(r"\bres(?:idue)?s?\s*#?\s*(\d{1,4})\b", re.I)
HIS = {"HIS", "HSD", "HSE", "HSP"}
AMINO = {"ALA", "ARG", "ASN", "ASP", "CYS", "GLN", "GLU", "GLY", "ILE", "LEU", "LYS", "MET", "PHE", "PRO", "SER",
         "THR", "TRP", "TYR", "VAL"} | HIS


def _ns(value: float, unit: str) -> float:
    return value if unit.lower().startswith("n") else value * 1000.0


def _time(ns: float) -> str:
    return f"{ns / 1000:.2f} µs" if ns >= 1000 else f"{ns:.0f} ns"


def _same(a: str, b: str) -> bool:
    return a == b or (a in HIS and b in HIS)


def residue_mentions(question: str) -> list[tuple[tuple[int, int], str, int]]:
    """Residues named in the question: (span in the text, three-letter name in capitals, number)."""
    out = []
    for m in RES_NAMED.finditer(question):
        name = (m.group(1) or m.group(2)).upper()
        if name in AMINO:
            out.append((m.span(), name, int(m.group(3))))
    return out


def residue_names(dataset: str) -> dict[int, str]:
    with TRAJ_LOCK:
        u = load_universe(dataset)
        return {int(r): str(n) for r, n in zip(u.residues.resids, u.residues.resnames)}


def near_numbers(n: int) -> list[int]:
    """Numbers one typo away from n, likeliest first: two neighbouring digits swapped, then one digit changed
    (closest first within each), dropped or added."""
    s = str(n)
    near = lambda c: abs(int(c) - n)  # noqa: E731
    swaps = sorted((s[:i] + s[i + 1] + s[i] + s[i + 2:] for i in range(len(s) - 1)), key=near)
    subs = sorted((s[:i] + d + s[i + 1:] for i in range(len(s)) for d in "0123456789"), key=near)
    drops = [s[:i] + s[i + 1:] for i in range(len(s))]
    adds = [s[:i] + d + s[i:] for i in range(len(s) + 1) for d in "0123456789"]
    out: list[int] = []
    for c in swaps + subs + drops + adds:
        if c and c[0] != "0" and int(c) != n and int(c) not in out:
            out.append(int(c))
    return out


def residue_check(question: str, dataset: str) -> dict | None:
    """The first named residue that doesn't match this protein, with the residues the user likely meant as
    choices (each one the question rewritten with it); None when every named residue matches."""
    names = residue_names(dataset)
    first, last = min(names), max(names)
    for (start, end), name, n in residue_mentions(question):
        actual = names.get(n)
        if actual and _same(name, actual):
            continue
        near = [k for k in near_numbers(n) if k in names and _same(name, names[k])][:3]
        labels = [f"{names[k]}{k}" for k in near] + ([f"{actual}{n}"] if actual else [])
        if actual:
            message = f"Residue {n} is {actual}, not {name}. Which residue did you mean?"
        elif labels:
            message = f"There is no residue {n}: this protein has residues {first}–{last}. Did you mean one of these?"
        else:
            message = f"There is no residue {n}: this protein has residues {first}–{last}. Ask again with one in that range."
        return {"verdict": "check_residue", "message": message, "examples": [], "ask_anyway": False,
                "why": question[start:end],
                "choices": [{"label": lb, "question": question[:start] + lb + question[end:]} for lb in labels]}
    return None


def data_notes(question: str, dataset: str) -> list[str]:
    cfg = dataset_config(dataset)
    with TRAJ_LOCK:
        n_frames = load_universe(dataset).trajectory.n_frames
    names = residue_names(dataset)
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

    # Planning through the API stops at residue_check() first; these notes cover the CLI and any other caller.
    for _, name, n in residue_mentions(question):
        if n not in names:
            notes.append(f"There is no residue {n}: this protein has residues {first}–{last}.")
        elif not _same(name, names[n]):
            notes.append(f"Residue {n} is {names[n]}, not {name}; the analysis uses {names[n]}{n}.")
    for num in RES_NUMBER.findall(question):
        if int(num) not in names:
            notes.append(f"There is no residue {int(num)}: this protein has residues {first}–{last}.")
    return list(dict.fromkeys(notes))
