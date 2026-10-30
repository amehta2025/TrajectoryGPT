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

## iyer2020_fig5c_adk_difference_contact_map.png — method-transfer figure

- **What it is:** panel C of Figure 5, **cropped from Fig 5** of the article below. It is the final differential contact map of adenylate kinase: red marks contacts found only in the closed structure, and blue marks contacts found only in the open one. Panels A and B (structure images) were cropped away. A few white rows were painted over the top edge to remove a sliver of panel A. Nothing else was changed. Downloaded on 2026-10-30 from the article's "large" figure image (1575 × 2119 px).
- **Source:** Iyer M, Li Z, Jaroszewski L, Sedova M, Godzik A (2020). Difference contact maps: From what to why in the analysis of the conformational flexibility of proteins. *PLOS ONE* 15(3): e0226702. **doi:10.1371/journal.pone.0226702**. https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0226702
- **License, as confirmed on the article page on 2026-10-30:** "© 2020 Iyer et al. This is an open access article distributed under the terms of the Creative Commons Attribution License, which permits unrestricted use, distribution, and reproduction in any medium, provided the original author and source are credited."
- **Their method (as stated in the article):**
  - Structures: closed 4jzkB vs open 4akeA (E. coli AdK).
  - Contact: any side-chain heavy atom pair within 5 Å (Cα for glycine).
  - Axes: positions in the SEQRES alignment, after dropping residues without coordinates or aligned to a gap.
  - The text says residue pairs whose inter-residue distance changed by ≤ 5 Å were filtered out. We could not reproduce this filter exactly (see the mapping notes in `notes/recon.md`).
- **How we use it:** as the method-transfer example. The agent applies the method to our trajectories, and the UI labels the result "Method applied to your data — numbers are not expected to match."
- **Axis mapping, checked 2026-10-30:** alignment position *p* (0-based) = E. coli AdK residue *p* + 1.
  - Both models resolve all 214 residues, and 4jzk and 4ake have identical SEQRES sequences, so the alignment has 214 gap-free positions.
  - All 76 coloured dots land on contacts recomputed from the two PDB models at this mapping. Shifting by ±1 position matches far fewer.
  - The article's named contact, Leu58–Glu170 (closed only), sits at positions (57, 169).

## Backup method-transfer candidate (documented only; not downloaded or used)

- Chen W, Lou J, Hsin J, Schulten K, Harvey SC, Zhu C (2011). Molecular Dynamics Simulations of Forced Unbending of Integrin αVβ3. *PLoS Comput Biol* 7(2): e1001086. **doi:10.1371/journal.pcbi.1001086**. https://journals.plos.org/ploscompbiol/article?id=10.1371/journal.pcbi.1001086
- **Figure 7C:** distance traces versus simulation time (ns) for the Asp393–Arg633 salt bridge and a nonpolar group, in free MD runs. Their distances are between side-chain centres of mass, not our minimum heavy-atom distance.
- **License, as confirmed on the article page on 2026-10-30:** "© 2011 Chen et al. This is an open-access article distributed under the terms of the Creative Commons Attribution License, which permits unrestricted use, distribution, and reproduction in any medium, provided the original author and source are credited."
