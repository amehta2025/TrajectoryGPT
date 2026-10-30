"""Check how the 'alignment position' axis of Iyer et al. 2020 Fig 5C maps to E. coli AdK residue numbers.

The figure (assets/figures/iyer2020_fig5c_adk_difference_contact_map.png, CC BY; see ATTRIBUTION.md) is a
difference contact map of AdK: red = contacts only in the closed structure (4jzk chain B), blue = only in the
open one (4ake chain A). Contact = any side-chain heavy-atom pair < 5 Å (Cα for glycine).

Method:
1. Find the plot frame in the cropped panel and read the red and blue dots as pixel centroids.
2. Download both PDB models (cached in cache/pdb/) and recompute closed-only and open-only contacts with their
   contact rule, over the residues resolved in both models.
3. Convert dot pixels to matrix positions (the 214 x 214 matrix fills the frame) and count how many dots land on
   a recomputed contact if position p = residue p + 1 + shift, for shift -1, 0, +1.

Expected (2026-10-30): shift 0 matches every dot, so alignment position p (0-based) = residue p + 1.
The article's ">5 Å distance change" filter is not applied: neither reading of it reproduces their dots.

Run from backend/:  python -m scripts.iyer_axis_mapping
"""
from __future__ import annotations

import sys
import urllib.request
import warnings
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from app.config import CACHE_DIR, REPO_ROOT

FIGURE = REPO_ROOT / "assets" / "figures" / "iyer2020_fig5c_adk_difference_contact_map.png"
PDB_DIR = CACHE_DIR / "pdb"
CLOSED, OPEN = ("4JZK", "B"), ("4AKE", "A")
CONTACT_A = 5.0
SHIFTS = (-1, 0, 1)
TOLERANCE = 1.0  # a dot matches a contact if both coordinates are within one matrix cell


@dataclass
class Frame:
    left: int
    right: int
    top: int
    bottom: int


# ---------- figure: frame and dots ----------

def load_rgb(path: Path = FIGURE) -> np.ndarray:
    return np.asarray(Image.open(path).convert("RGB")).astype(int)


def find_frame(img: np.ndarray) -> Frame:
    """The plot frame: the outermost long dark horizontal and vertical lines."""
    h, w, _ = img.shape
    dark = img.sum(axis=2) < 150
    rows = [y for y in range(h) if dark[y].sum() > w * 0.6]
    cols = [x for x in range(w) if dark[:, x].sum() > h * 0.5]
    if not rows or not cols:
        raise ValueError("no plot frame found")
    return Frame(min(cols), max(cols), min(rows), max(rows))


def _blobs(mask: np.ndarray, min_pixels: int = 4) -> list[tuple[float, float]]:
    """Centroids (x, y) of 4-connected pixel groups."""
    h, w = mask.shape
    seen = np.zeros_like(mask)
    out = []
    for y, x in zip(*np.nonzero(mask)):
        if seen[y, x]:
            continue
        stack, pts = [(y, x)], []
        seen[y, x] = True
        while stack:
            cy, cx = stack.pop()
            pts.append((cy, cx))
            for ny, nx in ((cy + 1, cx), (cy - 1, cx), (cy, cx + 1), (cy, cx - 1)):
                if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    stack.append((ny, nx))
        if len(pts) >= min_pixels:
            p = np.array(pts)
            out.append((float(p[:, 1].mean()), float(p[:, 0].mean())))
    return out


def read_dots(img: np.ndarray, frame: Frame) -> dict[str, list[tuple[float, float]]]:
    """Red and blue dot centroids (pixels) inside the frame."""
    r, g, b = img[..., 0], img[..., 1], img[..., 2]
    inside = np.zeros(r.shape, bool)
    inside[frame.top + 3:frame.bottom - 2, frame.left + 3:frame.right - 2] = True
    return {"red": _blobs(inside & (r > 180) & (g < 120) & (b < 120)),
            "blue": _blobs(inside & (b > 180) & (r < 120) & (g < 150))}


def to_positions(dots: list[tuple[float, float]], frame: Frame, n: int) -> list[tuple[float, float]]:
    """Pixel centroids -> 0-based matrix positions; the n x n matrix fills the frame, cell k centred at k + 0.5."""
    cw, ch = (frame.right - frame.left) / n, (frame.bottom - frame.top) / n
    return [((x - frame.left) / cw - 0.5, (y - frame.top) / ch - 0.5) for x, y in dots]


def count_matches(points: list[tuple[float, float]], pairs: list[tuple[int, int]], shift: int) -> int:
    """Dots that land within TOLERANCE of a contact (either triangle) when positions are offset by shift."""
    target = set(pairs) | {(j, i) for i, j in pairs}
    return sum(any(abs(px - (i + shift)) <= TOLERANCE and abs(py - (j + shift)) <= TOLERANCE for i, j in target)
               for px, py in points)


# ---------- structures: their contact rule ----------

def fetch_pdb(code: str, directory: Path = PDB_DIR) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{code}.pdb"
    if not path.exists():
        with urllib.request.urlopen(f"https://files.rcsb.org/download/{code}.pdb", timeout=60) as r:
            path.write_bytes(r.read())
    return path


def _residues(path: Path, chain: str) -> dict:
    import MDAnalysis as mda
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        u = mda.Universe(str(path))
    ag = u.select_atoms(f"protein and chainID {chain} and not altloc B C")
    return {r.resid: r for r in ag.residues}


def _side_chain(res):
    sel = "name CA" if res.resname == "GLY" else "not name N CA C O OXT and not name H*"
    return res.atoms.select_atoms(sel).positions


def contact_sets(closed_pdb: Path, open_pdb: Path) -> dict:
    """Closed-only and open-only contacts (0-based positions over residues resolved in both models)."""
    from MDAnalysis.lib.distances import distance_array
    rc, ro = _residues(closed_pdb, CLOSED[1]), _residues(open_pdb, OPEN[1])
    common = sorted(set(rc) & set(ro))
    n = len(common)

    def min_dist(rmap):
        pos = [_side_chain(rmap[k]) for k in common]
        m = np.full((n, n), np.inf)
        for i in range(n):
            for j in range(i + 1, n):
                if len(pos[i]) and len(pos[j]):
                    m[i, j] = m[j, i] = distance_array(pos[i], pos[j]).min()
        return m

    mc, mo = min_dist(rc), min_dist(ro)
    pairs = [(i, j) for i in range(n) for j in range(i + 1, n)]
    return {
        "resids": common, "names": {k: ro[k].resname for k in common}, "closed": mc, "open": mo,
        "closed_only": [(i, j) for i, j in pairs if mc[i, j] < CONTACT_A <= mo[i, j]],
        "open_only": [(i, j) for i, j in pairs if mo[i, j] < CONTACT_A <= mc[i, j]],
    }


# ---------- the check ----------

def check(figure: Path = FIGURE, pdb_dir: Path = PDB_DIR) -> dict:
    img = load_rgb(figure)
    frame = find_frame(img)
    dots = read_dots(img, frame)
    cs = contact_sets(fetch_pdb(CLOSED[0], pdb_dir), fetch_pdb(OPEN[0], pdb_dir))
    n = len(cs["resids"])
    points = {c: to_positions(d, frame, n) for c, d in dots.items()}
    sets = {"red": cs["closed_only"], "blue": cs["open_only"]}
    matches = {c: {s: count_matches(points[c], sets[c], s) for s in SHIFTS} for c in points}
    return {"frame": frame, "n_positions": n, "resids": cs["resids"], "dots": {c: len(p) for c, p in points.items()},
            "matches": matches, "points": points, "contacts": cs}


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    r = check()
    cs, n = r["contacts"], r["n_positions"]
    res = r["resids"]
    print(f"positions: {n}, residues {res[0]}-{res[-1]} resolved in both {CLOSED[0]}{CLOSED[1]} and {OPEN[0]}{OPEN[1]}"
          f"{'' if res == list(range(res[0], res[-1] + 1)) else ' (with gaps)'}")
    for c in ("red", "blue"):
        counts = ", ".join(f"shift {s:+d}: {m}/{r['dots'][c]}" for s, m in r["matches"][c].items())
        print(f"{c} dots: {counts}")
    best = {c: max(r["matches"][c], key=r["matches"][c].get) for c in r["matches"]}
    ok = all(b == 0 for b in best.values()) and all(r["matches"][c][0] == r["dots"][c] for c in r["dots"])
    i, j = res.index(58), res.index(170)
    print(f"anchor Leu58-Glu170 at positions ({i}, {j}): closed {cs['closed'][i, j]:.1f} Å, open {cs['open'][i, j]:.1f} Å, "
          f"closed-only={(i, j) in cs['closed_only']}")
    print(f"result: alignment position p (0-based) = residue p + {res[0]}" if ok else "result: mapping NOT confirmed")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
