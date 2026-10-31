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
- **Caption text:** `iyer2020_fig5c_caption.txt` (copied to `frontend/public/figures/`) holds the Fig 5 caption and the two Methods sentences that define the contact and the difference map, quoted verbatim from the article page on 2026-10-31, with the same attribution (CC BY 4.0). The sample button sends it with the image, because panel C alone carries no method.
- **How we use it:** as the method-transfer example. The agent applies the method to our trajectories, and the UI labels the result "Method applied to your data — numbers are not expected to match."
- **Axis mapping, checked 2026-10-30:** alignment position *p* (0-based) = E. coli AdK residue *p* + 1.
  - Both models resolve all 214 residues, and 4jzk and 4ake have identical SEQRES sequences, so the alignment has 214 gap-free positions.
  - All 76 coloured dots land on contacts recomputed from the two PDB models at this mapping. Shifting by ±1 position matches far fewer.
  - The article's named contact, Leu58–Glu170 (closed only), sits at positions (57, 169).

## Backup method-transfer candidate (documented only; not downloaded or used)

- Chen W, Lou J, Hsin J, Schulten K, Harvey SC, Zhu C (2011). Molecular Dynamics Simulations of Forced Unbending of Integrin αVβ3. *PLoS Comput Biol* 7(2): e1001086. **doi:10.1371/journal.pcbi.1001086**. https://journals.plos.org/ploscompbiol/article?id=10.1371/journal.pcbi.1001086
- **Figure 7C:** distance traces versus simulation time (ns) for the Asp393–Arg633 salt bridge and a nonpolar group, in free MD runs. Their distances are between side-chain centres of mass, not our minimum heavy-atom distance.
- **License, as confirmed on the article page on 2026-10-30:** "© 2011 Chen et al. This is an open-access article distributed under the terms of the Creative Commons Attribution License, which permits unrestricted use, distribution, and reproduction in any medium, provided the original author and source are credited."

## testset/ — figure-mode extraction test set (2026-10-31)

Panels cropped from open-access PLOS articles for `backend/scripts/figure_eval.py`, which measures how accurately figure mode reads a figure's method. Each license was confirmed on 2026-10-31 in the article's own metadata (PLOS article XML, `<permissions>`): all are distributed under the Creative Commons Attribution License (CC BY). Images were downloaded from the articles' "large" figure images and cropped to the panel named; nothing else was changed (the Chen crop also drops panel B's axis label). Each `.txt` beside an image holds the article's caption for that figure, quoted verbatim from the article XML, plus, where noted, one Methods sentence, with the same attribution. These figures are used only for testing and are not shown in the app.

- `milano2017_fig2a_rmsd.png`, `milano2017_fig2bc_rmsf.png` (Fig 2, panels A and B–C) and `milano2017_fig5_mindist.png` (Fig 5): Milano T, Gulzar A, Narzi D, Guidoni L, Pascarella S (2017). Molecular dynamics simulation unveils the conformational flexibility of the interdomain linker in the bacterial transcriptional regulator GabR from *Bacillus subtilis* bound to pyridoxal 5'-phosphate. *PLoS ONE* 12(12): e0189270. doi:10.1371/journal.pone.0189270. © 2017 Milano et al., CC BY. Note: the Fig 5 caption says Ile140B where the image and text say Ile104B.
- `chen2011_fig7c_distance.png` (Figure 7, panel C): Chen W, Lou J, Hsin J, Schulten K, Harvey SC, Zhu C (2011). Molecular Dynamics Simulations of Forced Unbending of Integrin αVβ3. *PLoS Comput Biol* 7(2): e1001086. doi:10.1371/journal.pcbi.1001086. © 2011 Chen et al., CC BY.
- `habibi2016_fig7a_native_contacts.png` (Fig 7, panel A): Habibi M, Rottler J, Plotkin SS (2016). *PLoS Comput Biol* 12(11): e1005211. doi:10.1371/journal.pcbi.1005211. © 2016 Habibi et al., CC BY. The `.txt` adds the Methods sentence defining a native contact.
- `perezhernandez2025_fig2b_contact_frequency.png` (Fig 2, panel B): Pérez-Hernández G, Hildebrand PW (2025). mdciao: Accessible Analysis and Visualization of Molecular Dynamics Simulation Data. *PLoS Comput Biol* 21(4): e1012837. doi:10.1371/journal.pcbi.1012837. © 2025 Pérez-Hernández, Hildebrand, CC BY. The `.txt` adds the Methods sentence defining the 4.5 Å closest-heavy-atom contact.
- The set also uses `iyer2020_fig5c_adk_difference_contact_map.png` (with `iyer2020_fig5c_caption.txt`) and `userguide_q1q2_replot.png`, attributed above.
