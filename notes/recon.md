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

## Statistics check: first half vs second half (Day 3, 2026-10-28)

`compare_intervals` on adk_equilibrium, windows frames 0–2092 vs 2093–4186 (≈ 502 ns each), cutoff 4.5 Å,
sequence exclusion 2, pooled-frequency filter ≥ 1%, BH-FDR 5%, seed 0, 2,000 resamples. Each run takes ~2 s on cached distances.

| groups | pairs | tested | filtered out | supported, τ from 0/1 only | supported, τ = max(0/1, distance) | too few blocks |
|---|---|---|---|---|---|---|
| NMP–CORE | 4,374 | 53 | 4,321 | 27 | **15** | 7 |
| LID–CORE | 5,542 | 29 | 5,513 | 14 | **14** | 0 |

- **τ from the distance series matters on NMP–CORE:** median τ over tested pairs (first half) is 11.9 frames
  from the 0/1 series and 42.6 frames from the min distance. Slow drifts near the cutoff look like fast flicker in 0/1.
  LID–CORE pairs flicker fast either way (median τ 0.9 vs 1.7 frames), so nothing changed there.
- **SER30–ASP84 is still supported:** frequency 0.461 → 0.148, Δ = −0.313, 95% CI [−0.450, −0.158], q = 7e-4,
  τ = 41.9 / 57.5 frames (≈ 10 / 14 ns), N_eff = 25 / 18 per window. It is the slow test's known strong change.
- The 7 "too few blocks" NMP–CORE pairs all had q ≥ 0.05, so the flag did not change the count today.
- **Coverage is still below nominal for slow pairs** (synthetic test, see SPEC §8a): about 72–84% for a nominal 95% CI.
  With 4τ blocks the supported counts would be 6 (NMP–CORE) and 13 (LID–CORE), but 24 NMP–CORE pairs,
  SER30–ASP84 included, would then have < 10 blocks. Not adopted; the limited-sampling caveat covers it.
- Single trajectory: "supported" means the two halves of *this* run differ, not that the protein's ensemble does.

### Provisional labels (Day 3, 2026-10-28)

Same comparison (first vs second half). Provisional = a window has < 20 bootstrap blocks, or τ_distance (the slower window)
is in the slowest quartile of the tested pairs. Supported provisional pairs display as "supported (slow — provisional)".

| groups | supported | firm | slow — provisional | provisional (all tested) | slowest-quartile τ_distance cutoff |
|---|---|---|---|---|---|
| NMP–CORE | 15 | 11 | 4 (3 for < 20 blocks, 1 for both reasons) | 24 of 53 | ≥ 87.4 frames (21.0 ns) |
| LID–CORE | 14 | 10 | 4 (all slowest-quartile only) | 8 of 29 | ≥ 4.8 frames (1.1 ns) |

- SER30–ASP84: **supported (slow — provisional)**. N_eff 25 → 18, τ 10.1 → 13.8 ns, fewer than 20 blocks per half.
- The quartile rule is relative: on LID–CORE it labels pairs with τ ≈ 1 ns "slow", although they have
  hundreds of blocks per window.

### CI coverage on synthetic slow-drift series (for README and SPEC §16)

Distance = cutoff + 0.4·AR(1) + fast Gaussian noise; true contact frequency is exactly 0.5. Windows of 2,000 frames.
Fraction of nominal 95% CIs that contain 0.5 (100–150 replicates per row):

| case | τ from 0/1 only (old) | τ = max(0/1, distance) (current) | 4τ blocks (not adopted) |
|---|---|---|---|
| φ = 0.98 (τ_AR ≈ 49), noise 0.6 Å | 0.44–0.56 | 0.56 | 0.65 |
| φ = 0.98, noise 0.2 Å | 0.72 | 0.72–0.75 | 0.81 |
| φ = 0.99 (τ_AR ≈ 99), noise 0.2 Å | 0.68 | 0.71 | — |
| φ = 0.967 (τ_AR ≈ 30), noise 0.1 Å | 0.76–0.78 | 0.82 | 0.86 |
| white noise | 0.94 | 0.94 | 0.93 |

The headline range quoted in SPEC is 56–76% (old) vs 72–84% (current) for the slow cases. The extreme
high-noise case (0.6 Å) is worse: about 0.56 even with the current rule. Longer windows don't close the
gap (coverage levels off around 0.84 with 2τ blocks at 20,000 frames): Sokal's window cuts off small, slow
autocorrelation tails.
