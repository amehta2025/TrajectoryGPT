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
   - block-bootstrap confidence intervals,
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
  - dataset.

  The backend enforces this; it is not left to the prompt.
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
- **Result (Day 1, see `notes/recon.md`):** LID–NMP contacts are nearly absent on `adk_equilibrium` (the top pair is in contact in 1.1% of frames; no pair is variable). The demo story centers on **NMP–CORE** (38 variable pairs), then **LID–CORE** (15). LID–NMP is used only as the closed-vs-open contrast with `adk_transition`. Group names stay parameters in code.

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
| `compare_intervals` | per-pair frequency change between two windows, block-bootstrap CI, **BH-FDR q-values**, supported or not |
| `contact_statistics` | for one pair: τ, N_eff, block-bootstrap CI of the frequency |
| `native_contacts` | fraction of native contacts vs frame against chosen reference frames (wraps `MDAnalysis.analysis.contacts`), for the §5c exact reproduction |
| `cutoff_sensitivity` | contact frequency per pair across cutoffs (stretch) |

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
- **τ:** integrated autocorrelation time with automatic windowing (Sokal, c = 5). It is computed on **both** the per-frame 0/1 contact series (τ_indicator) and the per-frame min-distance series (τ_distance). **τ = max(τ_indicator, τ_distance)** sets the block length and N_eff.
  - *Why* (changed Day 3, 2026-10-28): a distance that drifts slowly near the cutoff makes the 0/1 series flicker fast. τ_indicator alone then misses the slow memory, and the CIs come out too narrow. On NMP–CORE the median τ_distance is about 3.5× τ_indicator (42.6 vs 11.9 frames, first half).
  - Constant 0/1 series (always or never in contact) return `tau=None` and `stats_note`.
  - Series with fewer than 5 transitions are flagged "too few events for reliable CI."
  - A window with fewer than 10 bootstrap blocks (N / block length < 10) is flagged "too few independent blocks." With so few blocks, the moving-block bootstrap itself underestimates the spread.
  - **Known limitation (measured Day 3):** on synthetic slow-drift series (AR(1) with τ ≈ 50 frames, plus fast noise), nominal 95% CIs cover the truth only about 72–84% of the time with this rule (56–76% with τ_indicator alone). Sokal's window cuts off small, slow autocorrelation tails, and short windows can't measure their own slow variance. 4τ blocks raise coverage to about 80–88%, but on 500 ns windows they flag most slow pairs, SER30–ASP84 included. So "supported" can still be somewhat optimistic for slow pairs. These pairs are labelled **provisional** (below), and the report's limited-sampling caveat must say so.
- **N_eff** = N / (2τ), reported everywhere, shown next to **τ in ns** (τ × 0.24 ns × stride; path steps on `adk_transition`).
  - *Changed Day 3:* "N_eff × 0.24 ns" was dropped. It read as "6 ns of independent sampling" for SER30–ASP84 in a 502 ns window, which is misleading. τ in ns is the physical memory time.
- **Moving-block bootstrap:**
  - block length = max(1, ceil(2·τ_indicator), ceil(2·τ_distance)), 2,000 resamples
  - fixed seed, recorded in provenance
  - applied to frequencies and to between-window differences
  - the two windows are resampled independently
- **Multiple comparisons:** `compare_intervals` tests many pairs at once, so 95% CIs alone would produce false positives (about 5% of pairs).
  - **p-value:** two-sided z-test, z = Δ / SE, where SE is the standard deviation of the block-bootstrap differences. Then apply **Benjamini–Hochberg FDR at q < 0.05**.
    - *Why not a bootstrap-count p-value* (changed Day 3, 2026-10-28): with 2,000 resamples, such a p-value can't go below about 1/1,000. BH's strictest threshold is 0.05/m, which is about 1e-5 for NMP–CORE's 4,374 pairs. So BH could almost never declare anything, and the result would be a false "all noise." The z-test gives continuous p-values. The CI stays a bootstrap percentile CI.
  - **Pre-filter:** test only pairs that are not sequence-local and whose pooled contact frequency across both windows is ≥ 1%. Rare or never-formed contacts carry no testable signal and would only inflate m. The filter doesn't look at the difference between windows, so BH stays valid.
  - **"Supported"** = BH q < 0.05 **and** the percentile CI excludes 0 **and** the pair is not flagged "too few events" (< 5 transitions across both windows) **and** neither window is flagged "too few independent blocks" (< 10). A flagged pair is never supported, whatever its p or CI.
  - **Provisional** (added Day 3): a tested pair is provisional if either window has fewer than **20** bootstrap blocks, or its τ_distance (the slower window) is in the **slowest quartile** of the pairs tested in that comparison.
    - A supported pair that is provisional displays as **"supported (slow — provisional)"**; it still counts as supported.
    - The summary shows both counts ("15 supported: 11 firm · 4 slow — provisional"). An info tooltip explains the rule and cites the coverage numbers above.
    - The quartile is relative to the comparison, so about a quarter of tested pairs are always "slow" whatever their absolute τ. On LID–CORE the cutoff is about 1.1 ns.
    - `contact_statistics` (one pair, no quartile) uses only the < 20-blocks rule.
  - The UI says "N tested · M filtered out (rare or never in contact)".
- **Refusal:** if `stats_valid` is false, return `status: refused` with a one-sentence explanation the UI can show.
- **Validation tests:**
  - On AR(1) with known τ, the estimate is within 20%.
  - On white noise, the block CI ≈ the naive CI.
  - On a correlated series, the block CI is clearly wider.
  - On 500 pure-noise pairs, BH declares ≤ 5% supported.
  - On a slow-drift distance hovering at the cutoff (the 0/1 series flickers fast), τ_distance governs, and the block CI is ≥ 1.3× wider than with τ_indicator alone.
  - A series with fewer than 10 blocks is flagged and can never be supported.
  - On `adk_equilibrium`, a known strong change (SER30–ASP84, NMP–CORE, first vs second half) is supported (slow test), so the method is not "all noise."

## 9. Agent flow

1. **Input:** a question, *or* a figure image (+ optional methods PDF). Images and PDFs go to Muse Spark natively. If a file fails, fall back to `pypdf` text.
2. **Plan:** structured output (schema enforced) with up to 5 steps. Each step has a tool, params, rationale, and optional references to earlier steps' results.
   - In figure mode, the agent first returns an **extracted spec**: each field with its value, confidence, and source (caption / methods / axis / assumed).
   - It also declares whether this is an **exact reproduction** (the figure's data is one of our datasets) or a **method transfer**.
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
- **Chat**
- **PlanCard:** the plan checklist shows progress ("3/4 done")
- **StepLog**
- **Timeline:** click any turn to see what the model saw and what it called. A **"Step through turns"** toggle pauses the loop after each turn until the user clicks Next (§9b).
- **Heatmap:** clicking a cell highlights the residues and suggests a follow-up
- **DistancePlot:** with a cutoff line; clicking a point jumps to that frame
- **CompareChart:** CI bars; unsupported pairs greyed; shows the "N tested, FDR 5%" note
- **ReportView:** placeholders rendered with values; hovering a value shows its provenance
  - Each finding shows a "proven" / "unsupported" badge and an expandable "N citations" list. Clicking a citation highlights it in the chart or 3D view and shows its provenance.
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
  src/components/ Chat PlanCard StepLog Heatmap DistancePlot CompareChart ReportView ReproduceView Viewer VerifierBadge DatasetBadge ModelToggle CostMeter
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
| 4 | Sat Oct 31 | LLM wrapper (smoke-test Meta + Baseten first); planner with schema; PlanCard; turn loop with guardrails; bookkeeping tools; placeholders; StepLog streaming; ReportView; deterministic check; usage/cost logging | CLI and UI: question → plan → edit → run → live step log → report with filled values, in < 90 s, with the cost shown |
| 5 | Sun Nov 1 | Figure mode: image + PDF → extracted spec → plan → exact reproduction of the §5c target with a numeric match; method-transfer label; ReproduceView. **Checkpoint: the full demo path works on the deployed app.** | the reproduction matches the target within tolerance; the deployed app runs one full investigation |
| 6 | Mon Nov 2 | Muse Wonder verifier + overclaim toggle; follow-ups with session state; export script + Modal Sandbox verify; rate limit, budget, replay mode; record replay runs. **Feature freeze at midnight.** | the verifier flags the planted overclaim; the exported script reproduces the numbers in the sandbox; replay mode works with the LLM disabled |
| 7 | Tue Nov 3 | Baseten toggle (if the smoke test passed); README (setup, architecture diagram, built-during-hackathon, attributions, limitations); final video; **submit by 10 PM CT** | submission confirmed; repo public; video link works in a private window |
| 8 | Wed Nov 4 | Buffer: bug fixes only, X post, community vote push | — |

**Cut order if behind:**
1. §9d stretch items
2. `cutoff_sensitivity`
3. Baseten toggle
4. Modal Sandbox button (keep the export)
5. agent "add one step" adaptation
6. Muse Wonder model check (keep the deterministic check)
7. method-transfer mode (keep exact reproduction)

**Never cut:** linked viewer, statistics + FDR, the multi-step agent with evidence, exact figure reproduction, export, replay mode.

## 15. Demo video (3 minutes; rough cut Mon Nov 2, final Tue Nov 3)

The demo questions are finalized **after** Day 3. Per the recon (§5d), the live investigation (beats 2–3) centers on **NMP–CORE**, with **LID–CORE** as the backup. The skeleton:

1. **0:00–0:25.** Drop in the published figure. The extracted spec appears, our figure appears next to theirs, and the numbers match.
2. **0:25–1:05.** Ask a real question on the equilibrium data. The plan appears; edit the cutoff and approve. The step log runs, and the chart shows supported pairs vs greyed noise, with the "N tested, FDR 5%" note.
3. **1:05–1:40.** Click a supported pair and it highlights in 3D. Ask "does that persist?" The distance plot appears; click a point and the viewer jumps to that frame.
4. **1:40–2:05.** Show the honesty: N_eff with τ in ns, a "supported (slow — provisional)" pair and its tooltip, a stats refusal on the transition set, and the verifier catching a planted overclaim.
5. **2:05–2:35.** Export the script; the sandbox re-run matches.
6. **2:35–3:00.** Show the open-weight toggle (if shipped), the cost per investigation, and the built-during-hackathon summary.

Record with replay mode available as a fallback, but use **real live runs** for the recording.

## 16. Known limitations (state these in the README; judges respect honesty)

- One protein, two public trajectories. No uploads.
- Single trajectory: findings describe this simulation, not biology in general.
- Contact definition is fixed (min heavy-atom distance ≤ cutoff).
- Method-transfer reproductions are not expected to match numerically.
- Replay runs are recorded real runs, labeled as such.
- **Confidence intervals are optimistic for slowly varying contacts.**
  - On synthetic slow-drift series (AR(1), τ ≈ 50 frames, plus fast noise; 2,000-frame windows), nominal 95% block-bootstrap CIs contained the true frequency in only **72–84%** of trials with our rule (τ = max of indicator and distance τ, blocks of 2τ).
  - With the indicator τ alone it was **56–76%**.
  - Such pairs are labelled "supported (slow — provisional)" (SPEC §8a; numbers in `notes/recon.md`).

## 17. Out of scope

Running simulations; trajectory uploads; other proteins or formats; RMSD/RMSF; accounts; a database; executing model-written code; lab or private data; the C++ kernel (post-hackathon).