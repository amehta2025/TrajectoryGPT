# Data reconnaissance (SPEC §5d)

Rerun: `cd backend && python -m scripts.recon` (add `--dataset adk_transition` for the path).
Run on 2026-10-27, MDAnalysis 2.11.0, cutoff 4.5 Å, contact = min heavy-atom distance ≤ cutoff.
Pairs with |Δresid| ≤ 2 across a domain boundary are excluded from counts (close by chain geometry).

## Precompute timing (adk_equilibrium, 4,187 frames, first run, Windows laptop)

| groups | matrix | time |
|---|---|---|
| LID–NMP | 4187 × 38 × 30 | 3.8 s |
| LID–CORE | 4187 × 38 × 146 | 16.8 s |
| NMP–CORE | 4187 × 30 × 146 | 12.8 s |

Cached runs load in < 0.5 s. Dataset download (168 MB DCD) took about 2 min 15 s.

## Findings on adk_equilibrium

| groups | pairs ever in contact | variable (5–95%) | ≥ 50% | median transitions |
|---|---|---|---|---|
| LID–NMP | 23 | **0** | 0 | 4 |
| LID–CORE | 53 | 15 | 18 | 40 |
| NMP–CORE | 92 | 38 | 27 | 100 |

- **LID–NMP is almost empty**, as expected for apo AdK, which stays mostly open.
  The most frequent pair (LYS157–ASP54) is in contact in 1.1% of frames, with 5 transitions.
  These are rare, brief closing events: too few for statistics. This is not a demo story for contact frequencies.
- **NMP–CORE is the richest pair**: 38 variable pairs, many switching hundreds of times
  (e.g. LYS40–GLN74 at 0.477 with 1,498 transitions; ALA37–GLU70 at 0.598; THR31–PHE86 at 0.567).
  Many transitions mean many independent events, so N_eff should be healthy.
- **LID–CORE** is second: 15 variable pairs, mostly at the LID hinge
  (HSE134–ASP118 at 0.463; ARG123–ARG119 at 0.573; THR155–GLN160 at 0.576).
- Suggested demo default: **NMP–CORE**, with LID–CORE as a second example. LID–NMP is useful only
  as the "closed vs open" contrast against `adk_transition`, where it does form contacts
  (LYS157–ASP54 at 0.653 of path steps). Final choice of demo questions comes after Day 3 (SPEC §15).

## Data notes found during Day 1

- **PBC: not used on adk_equilibrium.** The DCD carries an 85.5 Å cubic box, but frames were fit
  (rotated) to CORE, so the box no longer matches the coordinates. Minimum-image distances changed
  about 500k LID–CORE atom distances (21 sampled frames) by up to 34 Å. The protein is whole (max bond 1.87 Å without PBC),
  so plain distances are correct. `config/dataset.yaml` sets `pbc: none` with this reason.
  This differs from SPEC §8 ("if a box exists, use minimum-image").
- Segids differ: `4AKE` on adk_transition, `ADK` on adk_equilibrium. Residue keys carry the segid, so keys from one dataset never match the other.
- Histidine is named `HSE` (CHARMM) on adk_equilibrium.

## Reproduction target (SPEC §5c)

**Exact reproduction: MDAnalysis User Guide, "Fraction of native contacts over a trajectory" / Q1 vs Q2.**
https://userguide.mdanalysis.org/stable/examples/analysis/distances_and_contacts/contacts_q1q2.html

- It uses the same files as `adk_transition` (`PSF`, `DCD` from MDAnalysisTests).
- Method: `contacts.q1q2(u, 'name CA', radius=8)`. Q1 is measured against frame 0 (closed), Q2 against the last frame (open).
  The output is a (98, 3) timeseries: frame, Q1, Q2.
- Checked on our install: it runs, giving Q1 = 1.000 → 0.932 and Q2 = 0.946 → 1.000 from first to last frame.
- **What "exact" means:** the page publishes plots, not a table of numbers. The numeric target is the
  timeseries that their published code produces. The `native_contacts` tool (Day 3) must match it
  (to float tolerance), and our plot should look the same as theirs.
- **License:** the UserGuide repo (github.com/MDAnalysis/UserGuide) is LGPL-2.1, a software license.
  It is unclear whether it covers reusing the rendered figure images. **Plan: don't embed their PNG.**
  Re-plot from their published code and data, and link the page. Record this in `assets/figures/ATTRIBUTION.md`
  when that folder is created.

**Method transfer (secondary):** not chosen yet. It needs a CC-BY paper figure; that's Day 5 scope.

## Statistics check: first half vs second half (Day 3, 2026-10-28; final, stats frozen)

`compare_intervals` on adk_equilibrium, windows frames 0–2092 vs 2093–4186 (≈ 502 ns each), cutoff 4.5 Å,
sequence exclusion 2, pooled-frequency filter ≥ 1%, BH-FDR 5%. Final method (SPEC §8a):
τ = max(τ_indicator, τ_distance); CI = Δ ± 1.96·sqrt(SE_A² + SE_B²) with SE = sqrt(p(1−p)·2τ/N);
provisional below 40 independent chunks (N / 2τ) in either window. The block bootstrap is kept only as a cross-check.

| groups | pairs | tested | filtered out | supported | firm | slow — provisional | provisional (all tested) |
|---|---|---|---|---|---|---|---|
| NMP–CORE | 4,374 | 53 | 4,321 | **0** | 0 | 0 | 39 |
| LID–CORE | 5,542 | 29 | 5,513 | **4** | 4 | 0 | 3 |

- **LID–CORE supported (all firm, hundreds of chunks per window):** HSE134–ASP118 (Δ −0.127, q 4e-4),
  GLY122–ASP118 (Δ −0.021), THR155–GLN160 (Δ −0.099), GLY122–ARG119 (Δ +0.031). HSE134–ASP118 is the slow test's known change.
- **SER30–ASP84 is no longer supported.** Δ = −0.313 (0.461 → 0.148), CI [−0.568, −0.059], q = 0.31.
  τ = 10.1 / 13.8 ns, so only 25 / 18 independent chunks per half. Label: provisional, not supported.
  Its own CI excludes 0, but after FDR across 53 tested pairs it does not pass.
- **NMP–CORE pairs are slow** (median τ_distance 42.6 frames vs τ_indicator 11.9 in the first half), so 39 of 53 are
  provisional. Several show large Δ with CIs that span 0 or don't survive FDR (e.g. VAL59–ARG88, MET34–LEU83, THR31–GLY85).
  On this trajectory, half-vs-half NMP–CORE changes are not established.
- **How the method evolved** (all superseded by the table above):
  - 2τ block bootstrap with τ_indicator: 27 (NMP–CORE) / 14 (LID–CORE) supported.
  - Adding τ_distance: 15 / 14.
  - The calibrated τ-based CI: 0 / 4.

  The bootstrap versions overclaimed: their CIs covered only 65–84% from 40 chunks up (notes/calibration/).
- Single trajectory: "supported" means the two halves of *this* run differ, not that the protein's ensemble does.

### CI calibration (for README and SPEC §8a / §16)

Rerun: `cd backend && python -m scripts.calibrate_ci --reps 200` (about 2 min). It writes `notes/calibration/coverage_by_process.csv`
and `coverage_by_chunks.csv`.

- Synthetic min distance = cutoff + offset + a·AR(1) + fast noise, so the true frequency is known.
  There are 8 processes (white, fast AR, slow and very slow drift with 0.1–0.6 Å noise, p = 0.5 and ≈ 0.23)
  × windows of 500–16,000 frames × 200 replicates.
- **τ-based CI coverage is ≥ 0.93 in every chunk bin from 40 up** (frequency 0.930–0.965, difference 0.942–0.977), and 0.86–0.92 below 40.
  So MIN_BLOCKS_FIRM = 40.
- **Bootstrap (2τ blocks) coverage from 40 chunks up: 0.74–0.81** (0.93 only above 1,000 chunks).
- Exception to the pooled threshold: slow drift with 0.6 Å fast noise on 500–1,000-frame windows covers 0.79–0.86
  despite 57–79 chunks. Listed in SPEC §16.
- **Correction:** an earlier version of these notes said the undercoverage came from Sokal's window cutting off slow
  autocorrelation tails, and quoted 61–87% / 49–82% coverage. The cause was wrong: τ estimates match the true τ on
  2-million-frame series (9.5 vs 9.6 and 89.5 vs 90.8 frames). The 2τ blocks were too short for the slow tail of
  the autocorrelation, so the bootstrap missed variance. Those percentages described the bootstrap and are superseded.
