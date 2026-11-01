"""Plots of stored tool results (matplotlib, no web or LLM imports): one figure per result.

Used for the verifier's PNGs and by the exported reproduce script, which bundles this module.
"""
from __future__ import annotations

import base64
import io

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from matplotlib.colors import LinearSegmentedColormap  # noqa: E402

from app.agent.tools import res_label  # noqa: E402

# The UI's colours for a change (frontend/src/theme.ts CHANGE_COLORS): lost orange, gained blue.
CHANGE_CMAP = LinearSegmentedColormap.from_list("change", ["#f0874f", "#151a24", "#5aa5f0"])


def png_b64(fig, dpi: int = 80) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode()


def plot_result(res: dict, time_label: str):
    """A matplotlib figure for one stored result, or None when the tool has no plot."""
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
        im = ax.imshow(m, aspect="auto", cmap="viridis" if tool == "calculate_contacts" else CHANGE_CMAP,
                       **({} if tool == "calculate_contacts" else {"vmin": -1, "vmax": 1}))
        fig.colorbar(im, ax=ax, label="contact frequency" if tool == "calculate_contacts"
                     else "Δ frequency (B − A): orange = lost, blue = gained")
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
    return fig
