# Figure attribution

Every figure in this folder (and its copy in `frontend/public/figures/`) is listed here with its source and license (SPEC §3, §5c).

## userguide_q1q2_replot.png — exact-reproduction sample figure

- **What it is:** our re-plot of the "Q1 vs Q2" example from the MDAnalysis User Guide. It was made by running the guide's published code, `contacts.q1q2(u, 'name CA', radius=8).run()`, on the same `PSF`/`DCD` files that ship with MDAnalysisTests. Their two plots (Q1 and Q2 vs frame; Q2 vs Q1) are redrawn with matplotlib by `backend/scripts/make_sample_figure.py`. Their PNG files are not copied.
- **Source page:** MDAnalysis User Guide, "Fraction of native contacts over a trajectory" (Q1 vs Q2), https://userguide.mdanalysis.org/stable/examples/analysis/distances_and_contacts/contacts_q1q2.html
- **Authors:** © 2019–2025 Lily Wang, Irfan Alibay, Patricio Barletta, Cédric Bouysset, Rubén Chaves, and the other User Guide contributors (full list on the page footer).
- **License, as confirmed on 2026-10-30:**
  - Code in the User Guide is under the GNU LGPL v2.1+.
  - Text and images are under CC BY-SA 4.0.
  - Sources: the repository README (https://github.com/MDAnalysis/UserGuide), which says "The code in the user guide is released under the GNU LGPL v2.1+ license, while text and images are released under the CC BY-SA 4.0 DEED license". The repository's `LICENSE` file contains both the LGPL v2.1 text and the CC BY-SA 4.0 text.
- **How we use it:** as the sample figure that figure mode reproduces. We regenerated it from their LGPL code rather than copying their image. If their original images are ever shown, they must carry this attribution, and any modified version must stay CC BY-SA 4.0.
- **Data:** the `adk_transition` trajectory (DIMS closed→open path of AdK). Beckstein O, Denning EJ, Perilla JR, Woolf TB (2009). *J Mol Biol* 394:160–176. doi:10.1016/j.jmb.2009.09.009. Distributed with MDAnalysisTests (LGPLv3+).

## Method-transfer figure

Not chosen yet. Candidates must be CC-BY or CC0 (no NC/ND), open access, and a residue contact map or distance trace. The license must be verified on the article page itself. Source, DOI and license go here before the figure is used.
