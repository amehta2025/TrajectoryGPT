# TrajectoryGPT — Build Spec (v4)

**TrajectoryGPT** is a Meta Global AI Developer Hackathon 2026 project in the Indie Developer track, under the "Push the Frontier" problem statement.
- Building period: Tue Oct 27 – Wed Nov 4, 2026. The official deadline is 8 PM CT on Nov 4.
- **Internal submit deadline: Tue Nov 3, 10 PM CT.**
- **Feature freeze: Mon Nov 2, 11:59 PM CT.**

This file is the source of truth. Build one day at a time (§14). Do not start a day's work until the previous day's "done when" check passes. If anything here is wrong or impossible, stop and say so.

---

## 1. Pitch

An AI research copilot for protein simulations. It turns a researcher's question, or a published figure, into a multi-step investigation. The investigation uses real, tested calculations, separates real effects from noise with honest time-series statistics, ties every claim to the computation that supports it, and links results to an interactive 3D view.

## 2. What must work (protect these; never cut)

1. **Linked investigation.** Clicking a heatmap cell or bar highlights those residues in 3D. Clicking a time point jumps the 3D view to that frame.
2. **Statistical honesty.** Every result reports:
   - the effective sample size,
   - autocorrelation-corrected confidence intervals, validated by a coverage experiment (§8a),
   - a multiple-comparison correction.

   Noise is labeled as noise, and the agent cannot call an effect "supported" unless the statistics say so.
3. **A multi-step agent with evidence.** One question becomes an approved plan. The model then chooses each tool call, and every finding cites the computations that support it.
4. **Reproduce a published figure.** This means an *exact* numeric reproduction of a published analysis of the same data (see §5c), plus "apply this figure's method to my trajectory" for figures made from other data.
5. **Reproducible export.** One click gives you a standalone script that recomputes every number.

## 3. Hackathon rules that affect the build

- The repo is **public**. Never commit API keys, `.env`, downloaded trajectories, or cache files.
- **New work only.** The README must have a "Built during the hackathon" section listing every component we built, with third-party libraries credited as dependencies.
- **Rights to data and assets.** Use only:
  - public datasets with their licenses respected (§5),
  - figures whose license allows reuse, with attribution in `assets/figures/ATTRIBUTION.md`.

  No lab data, ever. That includes sending it to Contributor-mode models.
- Must use Meta models: Muse Spark and Muse Wonder through the Meta Model API, and Muse Spark Open through Baseten.
- **Judges evaluate during Nov 5–8 and may use the live app.** The deployment must keep working and must not run out of credits (§12).

## 4. Engineering rules (non-negotiable)

- **The LLM never writes numbers.** It writes text with **placeholders** that reference results, e.g. `{{f3.delta}}` or `{{r12.ci_low}}`. Code fills in the values from stored tool results, with formatting and units. If text contains a bare numeral that isn't a residue number or a plan parameter, the deterministic check (§9 step 7) flags it.
- **No code execution from model output.** The agent can only call registered tools (§7). The export script is generated from **templates by our code**, never by the LLM.
- **No trajectory uploads.** Datasets are preloaded server-side. Users upload only a figure image and a methods PDF or text.
- **Consequential parameters need human approval:**
  - groups,
  - cutoff,
  - frame window and stride,
  - contact definition,
  - sequence exclusion (pairs with |Δresid| ≤ N are skipped; default `sequence_exclusion: 2` in `config/dataset.yaml`),
  - dataset,
  - the cutoff-robustness range (§7 `cutoff_sensitivity`).

  The backend enforces this; it is not left to the prompt.
- **No cutoff cherry-picking.** A cutoff is never chosen because it gives significance. A supported finding is reported as **robust** only if it is supported at *every* cutoff in the approved robustness range; otherwise it is **cutoff-sensitive**, and the cutoffs where it holds are shown. The agent's system prompt states this rule, and the deterministic check (§9 step 7) enforces it.
- **Units.** Distance is in Å. Time is in ns for the equilibrium dataset; the transition dataset has **no physical time**, so show "path step" and never invent ns. Frame index is always kept alongside.
- **Keep model context small.** The LLM receives *summaries* of tool results: top-N rows, statistics, provenance ids, and shapes. Full matrices go to the UI only. A tool result sent to the model must stay under about 2,000 tokens.

## 5. Data

### 5a. The protein
**Adenylate kinase (AdK)** from *E. coli* is a 214-residue enzyme whose LID and NMP domains close over the substrates ATP and AMP, then reopen. Its domains are commonly defined as:
- **NMP:** residues 30–59
- **LID:** residues 122–159
- **CORE:** the rest (1–29, 60–121, 160–214)

These come from Beckstein et al. 2009 and MDAnalysis teaching material. **Day 1 must confirm the ranges against a citable source and record the source in `config/dataset.yaml`.**

### 5b. Two datasets, two jobs

| | **`adk_equilibrium` (primary)** | **`adk_transition` (fixture + reproduction)** |
|---|---|---|
| What | 1.004 µs unbiased equilibrium MD of apo AdK (Anton, CHARMM27, solvent stripped, fit to CORE) | 98-frame closed→open transition path made with DIMS, a *biased* path-sampling method |
| Frames | 4,187, one every 0.24 ns | 98 path steps, not equally spaced in time |
| Get it | `MDAnalysisData.datasets.fetch_adk_equilibrium()`, about 161 MB, cached, **never committed** | `MDAnalysisTests.datafiles` `PSF`, `DCD` |
| License / cite | CC-BY 4.0. Seyler & Beckstein 2017, doi:10.6084/m9.figshare.5108170.v1 | Beckstein et al. 2009, *J Mol Biol* 394:160–176, doi:10.1016/j.jmb.2009.09.009 |
| Stats valid? | **Yes** | **No.** Biased and non-stationary. `stats_valid: false`; stats tools refuse with an explanation |
| Used for | All investigations and statistics | Fast tests, "closed vs open" contrast, exact figure reproduction |

`config/dataset.yaml` holds each dataset's:
- id, loader, and license/citation
- `stats_valid`
- `dt_ns` (`null` for transition)
- the domain ranges and their source

The dataset is a parameter on every tool, and the UI shows which dataset is active.

### 5c. Reproduction targets (decide on Day 1, by end of day)
- **Exact reproduction (primary demo):** the MDAnalysis User Guide's "Q1 vs Q2 contact analysis" example, run on the same `adk_transition` data.
  - Its figures (fraction of native contacts vs frame, and Q1 vs Q2) can be reproduced **numerically**, which proves the pipeline end to end.
  - Requires one extra tool: `native_contacts` (§7).
  - **Before showing their figure image in the app or video:** check the User Guide repo's license and record it in `ATTRIBUTION.md`. If reuse isn't allowed, re-plot from their published code and data instead, and link the page.
- **Method transfer (secondary):** an open-access (CC-BY) paper figure of a contact map or distance trace for any protein. The agent extracts the method and applies it to `adk_equilibrium`. The UI labels this **"Method applied to your data — numbers are not expected to match."**
- Store figures and attribution in `assets/figures/`.

### 5d. Data reconnaissance (Day 1; decides the demo story)
On `adk_equilibrium`, compute and print contact-frequency summaries for **LID–NMP, LID–CORE, and NMP–CORE** at 4.5 Å. Flag which pairs ever form contacts and how variable they are.
- If LID–NMP contacts are nearly absent, which is plausible for apo AdK, the demo uses the group pair with the richest behavior.
- **Do not hard-code any domain pair as "the" story.** Write the findings to `notes/recon.md`. This is the one allowed extra .md file.
- **Result (Day 1, see `notes/recon.md`):** LID–NMP contacts are nearly absent on `adk_equilibrium` (the top pair is in contact in 1.1% of frames; no pair is variable). Recon picked **NMP–CORE** (38 variable pairs), then **LID–CORE** (15). LID–NMP is used only as the closed-vs-open contrast with `adk_transition`. Group names stay parameters in code.
  - **Updated after Day 3 statistics (2026-10-28):** with calibrated CIs (§8a), the first-vs-second-half comparison finds **0** supported NMP–CORE changes and **4** firm LID–CORE changes. The live investigation therefore centers on **LID–CORE**, and NMP–CORE becomes the "honest null" (§15).

## 6. Stack and environment

| Layer | Choice |
|---|---|
| Frontend | React + TypeScript (Vite), deployed on Vercel |
| Charts | Plotly (`react-plotly.js`) in the UI; **matplotlib** for server-side PNGs (verifier input, export) |
| 3D viewer | NGL Viewer (§10a) |
| API | FastAPI, Python **3.11 or 3.12**, deployed on Modal |
| Science | MDAnalysis, NumPy, SciPy (`MDAnalysis.analysis.contacts` for native contacts) |
| LLM: agent | Meta Model API, `muse-spark-1.3`, **Responses API** (OpenAI Python SDK) |
| LLM: verifier | Meta Model API, `muse-wonder-1.0` |
| LLM: open-weight mode | Baseten, `meta-models/muse-spark-open-1026`, Chat Completions |
| Speech to text (stretch, §9d) | Meta Model API, `muse-voice-transcribe-1.0`, file endpoint `POST /v1/asr/transcribe` |
| Reproducibility check | Modal Sandbox |
| Storage | JSON files + `.npy` caches on a Modal Volume when deployed |

**Local environment:** the developer is on Windows.
- Use a project virtual environment (`python -m venv .venv`), and pin dependencies in `backend/requirements.txt`.
- Commands must work in both PowerShell and Git Bash, or the README gives both.

**Env vars** (`.env` locally, Modal Secrets when deployed, never in frontend code):
- `META_API_KEY`
- `META_API_BASE_URL=https://api.meta.ai/v1`
- `META_MODEL`: `muse-spark-1.3-contributor` for development, `muse-spark-1.3` for the demo and every deploy (§6b)
- `META_VERIFIER_MODEL=muse-wonder-1.0`
- `META_ASR_MODEL=muse-voice-transcribe-1.0` (stretch, §9d)
- `META_REASONING_EFFORT`: per call type, see §9c
- `BASETEN_API_KEY`
- `BASETEN_BASE_URL=https://inference.baseten.co/v1`
- `BASETEN_MODEL=meta-models/muse-spark-open-1026`
- `MODEL_PROVIDER=meta|baseten`
- `APP_MODE=live|replay`
- `DAILY_LLM_BUDGET_USD`
- `DEMO_PASSCODE`

### 6a. API reference material
Meta's docs live at dev.meta.ai/docs. The developer saves the needed pages as text in `docs_local/meta/`. That folder is **gitignored**; it's Meta's copyrighted documentation, for local reference only. The pages are:
- tool-calling
- structured-output
- image-understanding
- file-handling
- reasoning
- responses API
- error-handling

**Before writing any LLM code, read the relevant page there. If a needed page is missing, stop and ask; don't guess request shapes.**

### 6b. LLM wrapper (`app/agent/llm.py`)
- There is one interface: `run_turn(input_items, tools, *, schema=None, effort) -> TurnResult` (tool_calls | final_text | parsed), plus usage.
- **Meta path:** `client.responses.create(...)` with `include=["reasoning.encrypted_content"]` and `store=False` on every call. Append the model's output items, including reasoning, to the next turn's input unchanged (keep `phase` on assistant messages) so reasoning carries across the tool loop. `store=False` is the documented setting for stateless replay and avoids 413 errors when a request inlines a figure.
- **Baseten path:** Chat Completions with function tools, behind the same interface. Model ID `meta-models/muse-spark-open-1026` per the hackathon participant guide (Meta's docs say `meta-models/muse-spark-open`). Smoke-test tool calling and image input on Day 4 morning; if `-1026` fails, try `meta-models/muse-spark-open` and report which one worked. If tool calling is unreliable there, open-weight mode supports only the explain/verify steps, and the UI says so.
- Pass `api_key` explicitly.
- **Reasoning effort is always set explicitly** on every call (§9c). Muse Spark and Muse Wonder reject `none`; Baseten defaults to `xhigh`, which is slow if left unset.
- **Models by environment:** development may use `muse-spark-1.3-contributor`. Production and every deploy use `muse-spark-1.3` (Standard mode), because users upload figures and Contributor mode lets Meta train on inputs.
- **Verifier:** one Muse Wonder call per report. Muse Wonder has no Contributor mode, so its cost is logged separately.
- Timeouts: 120 s per call.
- **Retries are done by our code only;** SDK auto-retries are disabled (`max_retries=0`). Retry 429, 500, and 503 with exponential backoff and jitter, honoring `Retry-After`, max 3 tries. Never retry 504 (stream the call or lower the reasoning effort instead) or 400–413 (the request must be fixed).
- Record usage per call: input, cached, output, and reasoning tokens, model, latency, and estimated $.
- **Model prices live in one config table** (per 1M tokens, Standard and Contributor), used for every $ estimate, the CostMeter, and `DAILY_LLM_BUDGET_USD`.

## 7. Tools

Every tool:
- takes `dataset` plus its params,
- validates its inputs,
- returns `{status, summary_for_model, data_for_ui, params, units, provenance_id}`.

`status` is `ok`, `refused` (with a reason, e.g. stats on the transition dataset), or `error`.

**Science tools**

| Tool | Purpose |
|---|---|
| `get_metadata` | atoms, residues, n_frames, dt_ns / path steps, has_box, stats_valid, groups |
| `list_groups` | named groups and their residue ranges, with source |
| `calculate_contacts` | residue-pair contact frequency matrix for two groups over a frame window |
| `distance_over_time` | min heavy-atom distance per frame for one residue pair |
| `compare_intervals` | per-pair frequency change between two windows, autocorrelation-corrected CI, **BH-FDR q-values**, supported or not |
| `contact_statistics` | for one pair: τ, N_eff, autocorrelation-corrected CI of the frequency (block bootstrap as a cross-check) |
| `native_contacts` | fraction of native contacts vs frame against chosen reference frames (wraps `MDAnalysis.analysis.contacts`), for the §5c exact reproduction |
| `cutoff_sensitivity` | cutoff robustness for one or more pairs (below) |

**`cutoff_sensitivity` (core, Day 4):**
- Default range **4.0 / 4.5 / 5.0 Å**. The user can edit it on the PlanCard; every value must lie within **3.5–6.0 Å**, and the plan's main cutoff must be one of them.
- At each cutoff it reruns the same comparison (`compare_intervals`, or `contact_statistics` for one pair) on the **cached `min_dist`** with the **frozen §8a statistics**. No new statistics: BH-FDR is applied per cutoff, over the pairs tested at that cutoff, exactly as in a normal run.
- Per pair it returns Δ, CI, q, and supported / provisional at each cutoff, plus a verdict:
  - **robust:** supported at every cutoff in the range, with the same sign of Δ;
  - **cutoff-sensitive:** anything else, listing the cutoffs where it holds (e.g. "holds at 4.0, 4.5").
- Refuses on datasets with `stats_valid: false`, like the other stats tools.

**Analysis-view tools (post-checkpoint, §10b).** Same return shape; each one also feeds a UI view.

| Tool | Purpose |
|---|---|
| `contact_difference_map` | per-pair frequency change, window B − window A, for two groups; joins `compare_intervals` results so non-supported pairs can be de-emphasized |
| `contact_timeline` | 0/1 in-contact raster for up to ~20 pairs (top by \|Δ\|, or a given list) over a frame window |
| `domain_opening` | one opening coordinate per frame: centre-of-mass distance between LID or NMP and CORE (optionally the standard AdK domain angles, if a citable definition is found) |
| `contact_lifetimes` | lengths of contiguous in-contact runs for one pair or the top pairs; histogram and median lifetime |
| `residue_flexibility` | RMSF per residue over a frame window |

**Bookkeeping tools**

| Tool | Purpose |
|---|---|
| `update_checklist` | `item, status, note`; drives the live step log |
| `record_finding` | `text_with_placeholders, evidence_ids[]`; runs its deterministic checks immediately (every evidence id exists, every placeholder resolves) and refuses a bad finding with the reason |
| `submit_report` | `finding_ids[], caveats[], gaps[], follow_ups[]`; runs the full deterministic check (§9 step 7) before accepting. A failed check returns the specific problems as the tool result so the model can fix and resubmit. Ends the run only when accepted |

**UI-only:** `highlight(residues[], frames[])`.

**Contact definition (fixed, shown on every plan):** two residues are in contact in a frame if their minimum heavy-atom distance ≤ cutoff (Å).

## 8. Science implementation

- **Precompute** `min_dist[frame, resA, resB]` per `(dataset, groupA, groupB, selection)`.
  - Cache it to `cache/<hash>.npy` (gitignored), keyed by a hash of the inputs plus the library versions.
  - Show a progress message the first time. On `adk_equilibrium`, the LID–NMP run is about 4,187 frames × roughly 300 × 230 atoms, which is fine but takes time; measure it and record it in `notes/recon.md`.
- **PBC:** set per dataset in `config/dataset.yaml` (`pbc: none | minimum-image`), with the reason recorded there. Code never infers it from whether the file has a box. `adk_equilibrium` has a box, but its frames were fit (rotated) to CORE, so minimum-image distances are wrong; the protein is whole, so it uses `pbc: none` (details in `notes/recon.md`). If `minimum-image` is set and a frame has no box, it is an error.
- **Residue key:** `dataset:segid:resid:resname`. One function builds it and one parses it. The UI-to-NGL mapping uses `resid` (single chain), via one tested function (§10a).
- **Tests use `adk_transition`** so they run in seconds. One slow test, marked `@pytest.mark.slow`, checks the equilibrium precompute against a direct calculation on a few frames.

### 8a. Statistics (`app/science/stats.py`)
**Frozen for the hackathon (2026-10-28).** Further issues go into §16 Known limitations.

- **τ:** integrated autocorrelation time with automatic windowing (Sokal, c = 5). It is computed on **both** the per-frame 0/1 contact series (τ_indicator) and the per-frame min-distance series (τ_distance). **τ = max(τ_indicator, τ_distance)**.
  - *Why:* a distance that drifts slowly near the cutoff makes the 0/1 series flicker fast, and τ_indicator alone can then miss the slow memory. On NMP–CORE (first half) the median τ_distance is 42.6 frames, against 11.9 for τ_indicator.
  - Constant 0/1 series (always or never in contact) return `tau=None` and `stats_note`.
  - Series with fewer than 5 transitions are flagged "too few events for reliable CI."
- **Independent chunks** = N / (2τ) = N_eff: how many independent pieces of data a window holds. N_eff is reported everywhere, next to **τ in ns** (τ × 0.24 ns × stride; path steps on `adk_transition`). "N_eff × 0.24 ns" is not shown: it read as "6 ns of sampling" in a 502 ns window.
- **Confidence intervals (autocorrelation-corrected):** SE = sqrt(p(1−p) · 2τ / N), the variance of the mean of a correlated series. CI = p ± 1.96·SE, clipped to [0, 1].
  - **Differences** between two windows: SE = sqrt(SE_A² + SE_B²), CI = Δ ± 1.96·SE, clipped to [−1, 1].
  - **p-value:** two-sided z-test on z = Δ / SE.
  - **Block bootstrap: cross-check only.** A moving-block bootstrap (block length max(1, ceil(2τ)), fixed seed, windows resampled independently) is still computed and returned as `boot_*` fields. It is **not** used for "supported," because it undercovers (next bullet).
- **Calibration (Day 3; `backend/scripts/calibrate_ci.py`, tables in `notes/calibration/`):**
  - **Setup:** synthetic min-distance series with a known true frequency, distance = cutoff + offset + a·AR(1) + noise. There are 8 processes: white noise, fast AR(1), and slow and very slow drifts with 0.1–0.6 Å fast noise, at p = 0.5 and p ≈ 0.23. Each runs at window lengths of 500–16,000 frames, with 200 replicates per cell (about 9,600 replicates in total). The script runs the production functions and records whether each CI contains the truth (for differences, Δ = 0 between the halves of one series).
  - **Coverage of nominal 95% CIs, by independent chunks per window:**

    | chunks (N / 2τ) | τ-based, frequency | τ-based, difference | bootstrap (cross-check), frequency |
    |---|---|---|---|
    | 5–10 | 0.88 | 0.91 | 0.69 |
    | 10–20 | 0.86–0.87 | 0.91–0.92 | 0.66–0.71 |
    | 20–30 | 0.87 | 0.92 | 0.71 |
    | 30–40 | 0.88 | 0.96 | 0.70 |
    | **40–50** | **0.93** | **0.96** | 0.75 |
    | 50–200 | 0.93–0.94 | 0.94–0.98 | 0.74–0.78 |
    | 200–1000 | 0.94–0.96 | 0.96–0.97 | 0.76–0.81 |
    | ≥ 1000 | 0.97 | 0.96 | 0.93 |

  - **MIN_BLOCKS_FIRM = 40:** the smallest chunk count from which every bin covers ≥ 90%, for both frequencies and differences.
  - Below 10 chunks, a pair is never supported ("too few independent chunks of data").
  - *Correction (2026-10-28):* an earlier note blamed the undercoverage on Sokal's window cutting off slow autocorrelation tails. That was wrong. On 2-million-frame series our τ estimates match the true τ (for example 9.5 vs 9.6 frames, and 89.5 vs 90.8). The bootstrap was the problem: blocks of 2τ are shorter than the slow tail of the autocorrelation (about 50–250 frames), so neighbouring blocks stay correlated and the bootstrap misses that variance, however many blocks there are. Earlier coverage figures for the old rule (61–87%, and 49–82% with τ_indicator alone) described the 2τ bootstrap and are superseded by the table above.
- **Multiple comparisons:** `compare_intervals` tests many pairs at once, so 95% CIs alone would produce false positives (about 5% of pairs).
  - Apply **Benjamini–Hochberg FDR at q < 0.05** to the z-test p-values. The p-values are continuous: a bootstrap-count p-value (≥ about 1/2,000) could never pass BH's 0.05/m with thousands of pairs.
  - **Pre-filter:** test only pairs that are not sequence-local and whose pooled contact frequency across both windows is ≥ 1%. Rare or never-formed contacts carry no testable signal and would only inflate m. The filter doesn't look at the difference between windows, so BH stays valid.
  - **"Supported"** = BH q < 0.05 **and** the τ-based CI excludes 0 **and** not "too few events" (< 5 transitions across both windows) **and** not "too few independent chunks" (< 10 in either window). A flagged pair is never supported, whatever its p or CI.
  - **Provisional** = fewer than **MIN_BLOCKS_FIRM (40)** independent chunks in either window, where calibrated coverage is below 90%.
    - A supported provisional pair displays as **"supported (slow — provisional)"** and still counts as supported.
    - The summary shows "N supported: F firm · P slow — provisional", and the tooltip says "fewer than 40 independent chunks of data → less certain."
    - `contact_statistics` (one pair) uses the same rule.
  - The UI says "N tested · M filtered out (rare or never in contact)".
- **Result on `adk_equilibrium`, first vs second half (cutoff 4.5 Å):**
  - NMP–CORE: 53 tested, **0 supported**.
  - LID–CORE: 29 tested, **4 supported, all firm**.
  - SER30–ASP84 (Δ = −0.31, 18–25 chunks per half) is provisional and **not supported** (q = 0.31). The old bootstrap called it supported. Details in `notes/recon.md`.
- **Refusal:** if `stats_valid` is false, return `status: refused` with a one-sentence explanation the UI can show.
- **Validation tests:**
  - On AR(1) with known τ, the estimate is within 20%.
  - On white noise, the τ-based CI ≈ the naive CI.
  - On a correlated series, the CI is clearly wider.
  - The SE formula and the difference SE match their definitions.
  - On 300 correlated AR(1) series with known truth, the τ-based CI covers ≥ 90% (a fast version of the calibration).
  - On 500 pure-noise pairs, BH declares ≤ 5% supported.
  - On a slow-drift distance hovering at the cutoff, τ_distance governs, and the CI is ≥ 1.3× wider than with τ_indicator alone.
  - A series with fewer than 10 chunks is flagged and can never be supported.
  - On `adk_equilibrium` (slow tests): HSE134–ASP118 (LID–CORE) is supported and firm, so the method is not "all noise." SER30–ASP84 is provisional and not supported, so the method doesn't overclaim large but poorly sampled changes.

## 9. Agent flow

1. **Input:** a question, *or* a figure image (+ optional methods PDF). Images and PDFs go to Muse Spark natively. If a file fails, fall back to `pypdf` text.
2. **Plan:** structured output (schema enforced) with up to 5 steps. Each step has a tool, params, rationale, and optional references to earlier steps' results.
   - In figure mode, the agent first returns an **extracted spec**: each field with its value, confidence, and source (caption / methods / axis / assumed).
   - It also declares whether this is an **exact reproduction** (the figure's data is one of our datasets) or a **method transfer**.
   - **Example plan** for "Which LID–CORE contacts change between the first and second half?":
     1. `calculate_contacts` — LID–CORE, both halves, main cutoff
     2. `compare_intervals` — first vs second half (BH-FDR)
     3. `contact_statistics` — the top supported pair
     4. `cutoff_sensitivity` — the supported pairs, across the approved range (default 4.0 / 4.5 / 5.0 Å)
     5. `submit_report`

     After finding a supported pair, the agent checks cutoff robustness **before** submitting. The system prompt says so, along with the §4 no-cherry-picking rule.
3. **Validate (code):**
   - schema check
   - groups exist
   - frames in range
   - cutoff between 2.0 and 10.0 Å
   - tools allowed for the dataset (e.g. no stats on `adk_transition`)

   Invalid plans go back to the model once with the errors. If it fails again, show the user what's wrong.
4. **Approve once:** the PlanCard shows every step with editable params, the active dataset, and the contact definition. One **Run** click approves it all, and the approved params are stored server-side.
5. **Execute** with the turn loop (§9b):
   - The model chooses every call.
   - The backend rejects calls that change approved groups, cutoff, window, or dataset.
   - The model may skip an item (with a reason) or add **one** extra step using already-approved params.
   - A live step log streams to the UI.
6. **Report:** built only from recorded findings. The sections are:
   - Observed
   - Statistical support (N_eff, CI, q-value, supported or not)
   - Possible explanations (hedged, marked as hypotheses)
   - Caveats (always including single-trajectory and limited-sampling caveats)
   - Gaps: what the agent could not establish
   - Follow-ups, each a runnable plan step

   Placeholders are filled by code.
7. **Verify.** Verification gates submission (pattern from Meta's Agents 102 workshop). Only deterministic checks run inside the loop; they are fast.
   - **Deterministic check:**
     - every evidence id exists;
     - every placeholder resolves;
     - no bare numerals remain except residue numbers and approved params;
     - every "supported/significant/changed" claim cites a result where supported = true;
     - every "robust" claim cites a `cutoff_sensitivity` result whose verdict is robust; a supported finding without one is labelled "robustness not checked";
     - findings citing refused or errored results are flagged.
   - **Inside the loop:** `record_finding` runs the first two checks on each finding and refuses a bad one with the reason. `submit_report` runs the full check before accepting; if it fails, the tool result lists the specific problems so the model can fix them and resubmit.
   - **Refusal limit:** `MAX_REFUSALS = 2` refused `submit_report` calls. After the second refusal, or if the loop reaches `MAX_TURNS` without an accepted report, the run stops with status `needs_review` and the UI lists the unresolved problems.
   - **Model check (Muse Wonder), once, after acceptance, in the background:** it receives the report text, the findings with their resolved values, and one matplotlib PNG per figure, and flags claims that the data or plot doesn't support. The report shows immediately with a "Verifying…" badge that becomes ✅ verified or ⚠️ flagged (with the flags). The loop never waits on an LLM check.
   - **Report statuses:** `needs_review` | `awaiting_verification` → `verified` / `flagged`. The user gives the final sign-off.
   - The demo includes one intentionally overclaiming report, triggered by a dev-only toggle, to show the verifier catching it.
8. **Follow-ups:** the conversation state (last run, selected pair, approved params) is stored per session server-side. "Does that persist?" resolves to the selected or top supported pair and produces a new plan for approval.

### 9b. Turn loop (`app/agent/orchestrator.py`)
This is the same pattern as Meta's Agents 101 workshop. The loop is thin; all logic lives in tools.
```
run_investigation(session, question, approved_plan):
    run = new_run()                       # investigations/<run_id>.json
    input = [system prompt (stable, cached), user: question + plan as checklist]
    for turn in 1..MAX_TURNS (8):
        resp = llm.run_turn(input, TOOLS, effort=EFFORT["loop"])
        input += resp.output_items        # includes encrypted reasoning
        if no tool call: input += "Call exactly one tool."; continue
        result = tools.run(call, approved_plan)    # guardrails + deterministic checks here
        input += tool_result(result.summary_for_model)   # a refusal lists the problems
        save_progress(run, turn, call, result)     # → step log (SSE) + timeline
        if result.status == "submitted": break     # accepted report
        if call is submit_report and refused: refusals += 1
        if refusals == MAX_REFUSALS (2): run.status = "needs_review"; break
        if turn == MAX_TURNS - 1: input += "Last turn: call submit_report now."
        if run.step_through: wait_for_next()       # UI "Next" button
    if no accepted report: run.status = "needs_review"
    else: run.status = "awaiting_verification"; start_verifier_in_background(run)
    return run
```
- `resume_run(run_id)` continues from saved progress.
- Every turn's input (what the model saw) and output (what it called) is saved, for the UI timeline.
- **Step-through mode:** when the user turns it on, the loop pauses after each turn until the user clicks Next.
- CLI quick check: `python -m app.agent.cli run --turns 3 "<question>"` prints one line per turn.

### 9c. Latency and cost budget (measure on Day 4; adjust)
- Reasoning effort:
  - `low` for the loop turns
  - `medium` for planning and figure extraction
  - `high` only for the verifier
- **Deterministic checks are always on; LLM checks run once, async, and never inside the loop.**
- **Target: a full investigation completes in under 90 s.**
- Stream the step log so the user always sees progress.
- If it's too slow, reduce turns, lower effort, and send smaller summaries.
- Log $ per run. Print the cost of the last run in the CLI.

### 9d. Stretch (only if ahead, in order)
1. **Push-to-talk voice input.**
   - Hold **M** (only when focus is not in a text input) or hold the mic button to record; release to stop. Never always-listening.
   - A visible recording indicator shows while held; hint text "Hold M to ask by voice" sits near the viewer.
   - The browser sends the audio to our backend. The backend calls Muse Voice Transcribe's file endpoint (`POST /v1/asr/transcribe`) and returns the text into the chat box, where the user reviews it before sending. The API key stays server-side.
   - The endpoint accepts only mono 16-bit PCM WAV at 16 or 24 kHz, so the browser captures PCM and encodes WAV itself (no new package).
   - M is free: NGL's default keys are a, i, k, p, r, and Plotly binds no global shortcuts (checked 2026-10-28).
2. SAM 3.1 to crop the relevant panel from a multi-panel figure.
3. Search grounding (`web_search`) for literature-backed hypotheses, shown separately from computed results.

## 10. UI

Components:
- **StartScreen** (Day 4): what the user sees before any investigation.
  - A prominent input: "Ask a question about this simulation, or drop in a figure to reproduce."
  - The dataset picker beside it, with one line describing the protein and the dataset.
  - Three example-question buttons:
    1. "Which LID–CORE contacts change between the first and second half?"
    2. "Reproduce the native contacts figure", with the §5c sample figure pre-attached (only a figure cleared in `ATTRIBUTION.md`, §5c)
    3. "Is the SER30–ASP84 drop real?"
  - The viewer and charts move into a **results area** that appears once an investigation starts.
- **Chat**
- **PlanCard:** the plan checklist shows progress ("3/4 done"); the cutoff-robustness range is editable here (3.5–6.0 Å)
- **StepLog**
- **Timeline:** click any turn to see what the model saw and what it called. A **"Step through turns"** toggle pauses the loop after each turn until the user clicks Next (§9b).
- **Heatmap:** clicking a cell highlights the residues and suggests a follow-up
- **DistancePlot:** with a cutoff line; clicking a point jumps to that frame
- **CompareChart:** CI bars; unsupported pairs greyed; shows the "N tested, FDR 5%" note
- **ReportView:** placeholders rendered with values; hovering a value shows its provenance
  - Each finding shows a "proven" / "unsupported" badge and an expandable "N citations" list.
  - Each supported finding shows a **robustness badge**: "robust 4.0–5.0 Å", "cutoff-sensitive: holds at 4.0, 4.5", or "robustness not checked". Clicking a citation highlights it in the chart or 3D view and shows its provenance.
  - A "Gaps" section lists what the agent could not establish.
  - A `needs_review` run lists its unresolved problems instead of a finished report.
- **ReproduceView:** their figure on the left, ours on the right, the spec table, and the match result (exact mode) or the "method applied" label (transfer mode)
- **VerifierBadge:** "Verifying…" while the background check runs, then ✅ verified or ⚠️ flagged
- **DatasetBadge**
- **ModelToggle**
- **CostMeter:** small, showing $ per run

One shared selection store drives the plots and the viewer. The UI never calls an LLM directly; everything goes through the backend.

### 10a. 3D viewer data path (decided now so Day 2 doesn't stall)
- The backend serves a **reference structure** as PDB: `GET /structure/{dataset}`.
- **Coordinates:** `GET /frames/{dataset}?start&stop&stride` returns a compact binary Float32 array of protein heavy atoms (or all 3,341 atoms) for up to about 300 frames. The frontend adds these as an NGL trajectory/frames for playback.
  - Playback targets about 150 frames (stride 28 on the equilibrium set; set on Day 2 to keep the payload near 3 MB, which gzip barely shrinks). Jump-to-frame always fetches the exact frame, even if it isn't in the playback set. Responses are gzipped.
- **Atom order:** the PDB and the coordinate arrays are written by the **same** MDAnalysis AtomGroup, so atom order matches. This has a test.
- **Selection mapping:** `residueKeyToNglSelection(key)` returns `"<resid> and .CA"` (or the sidechain for licorice), from one function with a test fixture. Verify visually for 5 residues on Day 2.

### 10b. Analysis views (post-checkpoint; priority a → e)
Each view reuses the cached `min_dist` / contact data where possible, is linked to the shared selection store (click → highlight in 3D + distance plot + jump to frame), and is exposed to the agent as a tool (§7). Time axes use ns on `adk_equilibrium` and path step on `adk_transition` (§4).

- **a) Difference heatmap** (`contact_difference_map`). Window B minus window A contact frequency per pair (default: second half − first half), diverging colours for lost vs gained. Pairs that are not supported by `compare_intervals` are de-emphasized. On a dataset with `stats_valid: false` there is no "supported", so the map shows raw differences labelled "not statistically tested".
- **b) Contact timeline** (`contact_timeline`). A raster: the top ~20 pairs (by |Δ| or the user's selection) as rows, time on x, a mark wherever the pair is in contact. It shows coordinated breaking and forming. Clicking a mark jumps to that frame.
- **c) Domain opening over time** (`domain_opening`). One coordinate per frame: the centre-of-mass distance between LID (or NMP) and CORE, plotted with the viewer's current frame marked. The standard AdK domain angles are optional and used only if a citable definition is found (record it in `config/dataset.yaml`). Test: matches a hand calculation on a few `adk_transition` frames. The COM distance doesn't change under the CORE fit, since fitting is a rigid motion.
- **d) Contact lifetimes** (`contact_lifetimes`). A histogram of contiguous in-contact durations for a selected pair or the top pairs, and the median lifetime.
  - Durations are in ns on `adk_equilibrium` (run length × 0.24 ns × stride). Lifetimes shorter than one frame can't be seen, and runs touching the window edges are cut short (censored); both are stated next to the plot.
  - On `adk_transition` it **refuses**: path steps are biased and unevenly spaced in time, so a "lifetime" there has no meaning.
- **e) Flexibility** (`residue_flexibility`). RMSF per residue, coloured onto the 3D structure, plus a per-residue line plot.
  - On `adk_equilibrium` the frames are already fit to CORE, so RMSF is relative to CORE and LID/NMP motion shows up as flexibility; the plot says so.
  - On `adk_transition` the RMSF reflects the biased closed→open path, not equilibrium fluctuations; the plot is labelled "path RMSF, not equilibrium flexibility".

## 11. Provenance and export

- Every tool call is appended to `investigations/<run_id>.json`. Each entry records:
  - tool, params, and dataset id
  - dataset file hashes
  - seed
  - library versions
  - model, provider, and usage
  - timestamp
  - the summary and a pointer to the full result
- **Export:** `reproduce_<run_id>.py` is generated from templates by our code. It:
  - uses only MDAnalysis, MDAnalysisData, NumPy, SciPy, and matplotlib;
  - fetches the dataset;
  - recomputes every result (bootstrap included, same seed);
  - writes the plots;
  - prints a comparison table against the values embedded from the run.
- **Verify in clean environment:** run the export in a Modal Sandbox (dataset from a Modal Volume) and show a match within tolerance.

## 12. Deployment, reliability, and spend safety

- **Deploy early.** The first deploy is a skeleton (backend health + one tool + frontend) by the end of Day 3. Redeploy every day after.
- **Backend on Modal:**
  - FastAPI as an ASGI app.
  - The dataset is fetched into a Modal Volume once, and precompute caches live on the Volume.
  - Secrets live in Modal Secrets.
  - CORS allows only the Vercel domain.
  - `keep_warm=1` during judging if credits allow; otherwise show a "waking up" note.
- **Frontend on Vercel:** the backend URL comes from env; no keys in the frontend.
- **Abuse and credit protection (required before the public link is shared):**
  - Per-IP rate limit (e.g. 10 investigations/hour).
  - Global daily $ budget (`DAILY_LLM_BUDGET_USD`) tracked from usage logs. When it's exceeded, switch to replay mode.
  - A kill switch: setting env `APP_MODE=replay`.
  - An optional `DEMO_PASSCODE` for live mode; judges get it in the submission notes.
- **Replay mode:** 3–5 curated investigations recorded from real live runs, with full step logs and results, play back without calling any LLM. They are clearly labeled "Recorded run." The science tools, viewer, and charts stay live. This keeps the app useful during judging even if the API or credits fail.
- **Health page:** `/health` shows the API reachable, the dataset loaded, the mode, and today's spend.

## 13. Repo layout

```
/backend
  app/main.py  app/config.py
  app/science/   loader.py residues.py contacts.py distances.py stats.py native_contacts.py structure.py
  app/agent/     llm.py tools.py orchestrator.py prompts.py verifier.py cli.py session.py
  app/provenance/ record.py export.py templates/
  app/safety/    ratelimit.py budget.py replay.py
  tests/
  requirements.txt  modal_app.py
/frontend  (Vite React TS)
  src/components/ StartScreen Chat PlanCard StepLog Heatmap DistancePlot CompareChart ReportView ReproduceView Viewer VerifierBadge DatasetBadge ModelToggle CostMeter
                  (post-checkpoint, §10b) DifferenceHeatmap ContactTimeline DomainOpening ContactLifetimes Flexibility
  src/state/  src/api/
/config/dataset.yaml
/assets/figures/  (+ ATTRIBUTION.md)
/notes/recon.md
/docs_local/      (gitignored)
/cache/  /investigations/  (gitignored)
README.md  CLAUDE.md  SPEC.md  .env.example  .gitignore
```

## 14. Schedule

Each day is one or more Claude Code sessions. Use `/clear` between unrelated tasks.

| Day | Date | Build | Done when |
|---|---|---|---|
| 1 | Tue Oct 27 night – Wed Oct 28 | venv + requirements; `dataset.yaml` with confirmed domain ranges; loaders for both datasets; residue keys; precompute + cache; `calculate_contacts`; `distance_over_time`; **recon (§5d)**; pick the reproduction target (§5c) | `pytest -q` passes (fast tests on the transition set); 3 pairs match a direct hand calculation; the equilibrium precompute runs and its timing is recorded; `notes/recon.md` written |
| 2 | Thu Oct 29 | FastAPI endpoints for metadata/contacts/distances/structure/frames; frontend scaffold; NGL viewer (§10a); heatmap + distance plot; click→highlight; jump-to-frame | 5 heatmap clicks highlight the correct residues (checked by eye against residue labels); clicking a distance point moves the viewer |
| 3 | Fri Oct 30 | `stats.py` + tests; `compare_intervals` (BH-FDR); `contact_statistics`; refusal on the transition set; CompareChart; `native_contacts`; **skeleton deploy (Modal + Vercel)** | all stats validation tests pass; the live URL shows the heatmap from the deployed backend |
| 4 | Sat Oct 31 | LLM wrapper (smoke-test Meta + Baseten first); planner with schema; PlanCard; turn loop with guardrails; bookkeeping tools; placeholders; StepLog streaming; ReportView; deterministic check; usage/cost logging; **StartScreen** (§10); **`cutoff_sensitivity`** + robustness badge + no-cherry-picking rule in the prompt and the check (§4, §7) | CLI and UI: question → plan → edit → run → live step log → report with filled values, in < 90 s, with the cost shown; the start screen's example buttons start investigations; a supported pair gets a robustness verdict |
| 5 | Sun Nov 1 | Figure mode: image + PDF → extracted spec → plan → exact reproduction of the §5c target with a numeric match; method-transfer label; ReproduceView. **Checkpoint: the full demo path works on the deployed app.** Then, after the checkpoint: the **Design pass** (§14a), then the §10b analysis views in priority order: (a) difference heatmap → (b) contact timeline → (c) domain opening → (d) contact lifetimes → (e) flexibility | the reproduction matches the target within tolerance; the deployed app runs one full investigation; the Design pass checks in §14a pass; each built view is linked to the 3D viewer and callable by the agent |
| 6 | Mon Nov 2 | Muse Wonder verifier + overclaim toggle; follow-ups with session state; export script + Modal Sandbox verify; rate limit, budget, replay mode; record replay runs. **Feature freeze at midnight.** | the verifier flags the planted overclaim; the exported script reproduces the numbers in the sandbox; replay mode works with the LLM disabled |
| 7 | Tue Nov 3 | Baseten toggle (if the smoke test passed); README (setup, architecture diagram, built-during-hackathon, attributions, limitations); final video; **submit by 10 PM CT** | submission confirmed; repo public; video link works in a private window |
| 8 | Wed Nov 4 | Buffer: bug fixes only, X post, community vote push | — |

**Cut order if behind:**
1. §9d stretch items
2. §10b (e) flexibility
3. §10b (d) contact lifetimes
4. §10b (c) domain opening
5. §10b (b) contact timeline
6. §10b (a) difference heatmap
7. Baseten toggle
8. Modal Sandbox button (keep the export)
9. agent "add one step" adaptation
10. Muse Wonder model check (keep the deterministic check)
11. method-transfer mode (keep exact reproduction)

**Never cut:** linked viewer, statistics + FDR, the multi-step agent with evidence, exact figure reproduction, export, replay mode, the Design pass (§14a; the demo video depends on it).

### 14a. Design pass (Day 5, right after the checkpoint, before the §10b views)
Protected from cutting: the video (§15) is recorded on this UI.
- **Answer-first layout.** The question, then the report at the top: headline findings in plain language. The evidence sits below: charts, 3D viewer, plan, step log, timeline.
- **Plain-language UI.**
  - No internal names in primary text: no `stats_valid`, `stride`, tool names, provenance ids or run statuses as headings or labels.
  - Advanced parameters (stride, sequence exclusion, windows in frames, robustness cutoffs) are collapsed under **"Settings"**.
  - Plan steps are plain sentences (e.g. "Compare contacts between the first and second half"), with the tool name as a small secondary label.
- **Clear visual hierarchy and a distinctive, non-template look.** Deliberate typography and spacing. Colour is restrained and used only for meaning: lost vs gained contact, supported vs noise, robust vs cutoff-sensitive.
- **Process.** Use the `design:ux-copy` skill for labels and plan-step wording, and run `design:design-critique` on each screen after changing it (frontend-design is not required). Check every screen in the browser at 1440 px wide and on a laptop screen (about 1280–1366 px): start screen, passcode prompt, plan, running, report, `needs_review`, and the transition dataset's descriptive report.
- **Done when:** every screen above is checked at both widths. No internal name appears in primary text. The report's headline is readable without scrolling at 1440 px.

## 15. Demo video (3 minutes; rough cut Mon Nov 2, final Tue Nov 3)

Demo story (set after Day 3, see §5d and `notes/recon.md`):
- The live investigation (beats 2–3) centers on **LID–CORE**, featuring **HSE134–ASP118**.
  - First vs second half at 4.5 Å: frequency 0.527 → 0.399, Δ −0.127, CI [−0.185, −0.070], q = 4e-4, supported and firm (hundreds of independent chunks per window).
  - It stays supported and firm at 4.0 Å (q = 0.001) and 5.0 Å (q = 0.006), so it is **robust across 4.0–5.0 Å** (the default range). It is **not** supported at 5.5 Å, where the contact is almost always formed (0.98 → 0.97); if a range reaching 5.5 Å is used, it shows as cutoff-sensitive, and we say so rather than avoid it.
- **NMP–CORE is the honest null** in beat 4.

The skeleton:

1. **0:00–0:25.** Drop in the published figure. The extracted spec appears, our figure appears next to theirs, and the numbers match.
2. **0:25–1:05.** Ask a real question on the equilibrium data about **LID–CORE** contacts (e.g. "Which LID–CORE contacts change between the first and second half of the run?"). The plan appears, showing the cutoff-robustness range (4.0 / 4.5 / 5.0 Å); approve it. The step log runs, and the chart shows the few supported pairs vs greyed noise, with the "N tested, FDR 5%" note. If built, the **difference heatmap** (§10b a) shows lost vs gained contacts with noise de-emphasized. The report's robustness badge reads **"robust 4.0–5.0 Å"** for HSE134–ASP118.
3. **1:05–1:40.** Click **HSE134–ASP118** and it highlights in 3D. Ask "does that persist?" The distance plot appears; click a point and the viewer jumps to that frame. If built, the **domain-opening plot** (§10b c) marks the same frame, showing how open LID is at that moment.
4. **1:40–2:05.** Show the honesty:
   - **The honest null: NMP–CORE.** **SER30–ASP84** looks like a big drop (0.46 → 0.15, Δ = −0.31), and its own 95% CI [−0.57, −0.06] excludes 0. But its contacts change slowly (τ ≈ 10–14 ns), so each half holds only about 25 / 18 independent chunks. After correcting for 53 pairs tested, q = 0.31: **not supported**, labelled provisional.
     - Show the CI and q side by side (the CompareChart hover shows both).
     - Say it plainly: "the frames are correlated and we tested 53 pairs, so this drop isn't established."
     - All 53 NMP–CORE pairs come out unsupported.
   - Also show N_eff with τ in ns, the "fewer than 40 independent chunks" tooltip, a stats refusal on the transition set, and the verifier catching a planted overclaim.
5. **2:05–2:35.** Export the script; the sandbox re-run matches.
6. **2:35–3:00.** Show the open-weight toggle (if shipped), the cost per investigation, and the built-during-hackathon summary.

Record with replay mode available as a fallback, but use **real live runs** for the recording.

## 16. Known limitations (state these in the README; judges respect honesty)

- One protein, two public trajectories. No uploads.
- Single trajectory: findings describe this simulation, not biology in general.
- Contact definition is fixed (min heavy-atom distance ≤ cutoff).
- Method-transfer reproductions are not expected to match numerically.
- Replay runs are recorded real runs, labeled as such.
- **Statistics describe this one simulation, with calibrated but imperfect intervals.**
  - CIs are autocorrelation-corrected (SE from τ). In a coverage experiment with known answers (about 9,600 synthetic series, `notes/calibration/`), nominal 95% CIs covered 93–97% from 40 independent chunks per window upward, but only 86–88% below that. Such results are labelled "provisional."
  - The 40-chunk threshold is pooled across processes. One case, a slow drift under large fast noise on short windows (500–1,000 frames), covered only 79–86% despite 57–79 chunks.
  - The first statistics version used a 2τ block bootstrap, which covered only 65–84%. It is kept as a cross-check field and is not used for decisions.
  - With these CIs, the first-vs-second-half comparison finds no supported NMP–CORE change and 4 LID–CORE changes. Large-looking slow changes such as SER30–ASP84 stay unsupported, because a 500 ns window holds only about 20 independent chunks of them.

## 17. Out of scope

Running simulations; trajectory uploads; other proteins or formats; RMSD; accounts; a database; executing model-written code; lab or private data; the C++ kernel (post-hackathon).