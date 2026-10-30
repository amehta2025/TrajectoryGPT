"""Regenerate the MDAnalysis User Guide "Q1 vs Q2" figure from its published code (SPEC §5c).

Source: https://userguide.mdanalysis.org/stable/examples/analysis/distances_and_contacts/contacts_q1q2.html
Their code (LGPL-2.1+): contacts.q1q2(u, 'name CA', radius=8).run(), then pandas .plot() of
Q1 and Q2 vs Frame and of Q2 vs Q1. We run the same calculation and draw the same two plots with
matplotlib (no pandas), as one two-panel PNG. This is the sample figure for figure mode.

Run from backend/:  python -m scripts.make_sample_figure
"""
import shutil

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import MDAnalysis as mda  # noqa: E402
from MDAnalysis.analysis import contacts  # noqa: E402
from MDAnalysisTests.datafiles import DCD, PSF  # noqa: E402

from app.config import REPO_ROOT  # noqa: E402

OUT = REPO_ROOT / "assets" / "figures" / "userguide_q1q2_replot.png"
PUBLIC = REPO_ROOT / "frontend" / "public" / "figures" / OUT.name


def main() -> None:
    u = mda.Universe(PSF, DCD)
    ts = contacts.q1q2(u, "name CA", radius=8).run().results.timeseries  # columns: Frame, Q1, Q2
    fig, (a, b) = plt.subplots(1, 2, figsize=(10, 4), dpi=150)
    a.plot(ts[:, 0], ts[:, 1], label="Q1")
    a.plot(ts[:, 0], ts[:, 2], label="Q2")
    a.set_xlabel("Frame")
    a.set_ylabel("Fraction of native contacts")
    a.legend()
    b.plot(ts[:, 1], ts[:, 2])
    b.set_xlabel("Q1")
    b.set_ylabel("Q2")
    fig.suptitle("Fraction of native contacts (Q1: closed reference, Q2: open reference)", fontsize=10)
    fig.text(0.01, 0.01, "Regenerated from the MDAnalysis User Guide's published code (LGPL-2.1+); "
             "original page text and figures CC BY-SA 4.0.", fontsize=6, color="0.4")
    fig.tight_layout(rect=(0, 0.03, 1, 0.95))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT)
    PUBLIC.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(OUT, PUBLIC)
    print(f"wrote {OUT} and {PUBLIC}; Q1 {ts[0, 1]:.3f}->{ts[-1, 1]:.3f}, Q2 {ts[0, 2]:.3f}->{ts[-1, 2]:.3f}")


if __name__ == "__main__":
    main()
