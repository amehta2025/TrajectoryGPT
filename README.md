Meta Hackathon Project

## Figure checks

- **Method-transfer figure axis mapping.** `backend/scripts/iyer_axis_mapping.py` checks how the "alignment
  position" axis of Iyer et al. 2020, Fig 5C (`assets/figures/`, CC BY; see `ATTRIBUTION.md`) maps to E. coli AdK
  residue numbers.
  - It reads the red and blue dots from the cropped panel.
  - It downloads the two PDB models the figure was made from (4jzk chain B closed, 4ake chain A open) into `cache/pdb/`.
  - It recomputes their contacts (any side-chain heavy atom pair within 5 Å; Cα for glycine).
  - It counts how many dots land on a contact for each possible offset.
  - Result: alignment position *p* (0-based) = residue *p* + 1. All 76 dots match, and the paper's Leu58–Glu170
    contact is at (57, 169).
  - Run it from `backend/` with `python -m scripts.iyer_axis_mapping`. Its tests are in
    `backend/tests/test_iyer_axis_mapping.py`; the full check is marked slow because it downloads from RCSB.

## Known limitations

(The full list from SPEC §16 is added on Day 7.)

- **Plan steps share one set of approved parameters.** The plan's dataset, groups, cutoff, windows, stride, sequence exclusion and robustness cutoffs apply to every step, and the backend enforces them. Steps do not carry parameters of their own. The agent picks per-call details within those limits, such as which residue pair to examine.
