# TrajectoryGPT — Build Spec (v3)

Working name: **TrajectoryGPT**. This is a Meta Global AI Developer Hackathon 2026 project in the Indie Developer track, under the "Push the Frontier" problem statement.
Building period: **Tue Oct 27 – Wed Nov 4, 2026** (deadline 9 PM ET / 8 PM CT). Internal deadline: **Tue Nov 3**.

This file is the source of truth. Build one day at a time (§13). Do not start a day's work until the previous day's "done when" check passes.

---

## 1. One-sentence pitch

An AI research copilot that reproduces a published molecular dynamics analysis from a paper's figure, runs verified calculations on a real trajectory, refuses to overclaim with honest time-series statistics, and links every result to an interactive 3D molecular view.

## 2. The four things that make this win (protect these)

1. **Reproduce-this-figure.** The user uploads a paper figure (image) and its methods text (PDF or pasted text). Muse Spark extracts the analysis spec (groups, cutoff, time window, plot type) and proposes a plan. After the user confirms, the app runs the analysis and shows the paper's figure next to ours.
2. **Linked investigation.** Clicking a heatmap cell or bar highlights those residues in the 3D viewer. Clicking a time point jumps the viewer to that frame.
3. **Statistical honesty.** MD frames are autocorrelated, so we report the effective sample size and block-bootstrap confidence intervals. The agent is not allowed to call a change "significant" unless the CI supports it.
4. **Verifiable and reproducible.** A verifier pass checks every claim against the tool output and the rendered plot. One-click export produces a standalone script that reproduces every number, and it can be re-run in a clean Modal Sandbox.

## 3. Hackathon rules that affect the build

- The repo is **public**. Never commit API keys; `.env` stays in `.gitignore`.
- **New work only.** The first code commit is on Oct 27. The README must have a "Built during the hackathon" section listing what we built, with third-party libraries credited as dependencies.
- **Data and assets must be ours to use.** That means:
  - The AdK dataset from `MDAnalysisTests`.
  - Paper figures only from open-access (CC-BY) papers or the MDAnalysis docs, with attribution.
  - No lab data in the repo.
- Must use Meta models: Muse Spark via the Meta Model API (default), and Muse Spark Open via Baseten (open-weight mode).

## 4. Non-negotiable engineering rules

- **The LLM never produces numbers.** Every displayed value comes from a deterministic tool function.
- **No arbitrary code execution.** The agent can only call the tool registry (§7). No `eval`, and no running generated code on the server. (The exported script runs only inside a Modal Sandbox, and only when the user clicks.)
- **No uploads of trajectories.** The dataset is preloaded server-side. Users may upload only a figure image and methods text.
- **Consequential parameters require confirmation.** These are groups, cutoff, frame range/stride, and contact definition. The agent proposes, a PlanCard shows the plan, and nothing runs until the user clicks Run.
- **Units are explicit everywhere.** Use Å and ns (with frame index alongside).

## 5. Dataset

- AdK closed→open trajectory: `PSF`, `DCD` from `MDAnalysisTests.datafiles`.
- Groups: the **LID** vs. **NMP** domains (plus **CORE** if useful). Their inter-domain contacts change across the transition.
- Verify the domain residue ranges against the MDAnalysis docs or literature, and store them in `config/dataset.yaml`.
- Record the atom count, frame count, timestep, and whether a box/PBC exists. This trajectory likely has no box; handle both cases anyway.

## 6. Stack

| Layer | Choice |
|---|---|
| Frontend | React + TypeScript (Vite), deployed on Vercel |
| Charts | Plotly (`react-plotly.js`) |
| 3D viewer | NGL Viewer |
| API | FastAPI (Python 3.11+), deployed on Modal |
| Science | MDAnalysis, NumPy, SciPy |
| LLM (agent loop) | Meta Model API, `muse-spark-1.3`, via the **Responses API** (OpenAI Python SDK) |
| LLM (verifier) | Meta Model API, `muse-wonder-1.0`: a different, stronger model checks the agent's work |
| LLM (open-weight mode) | Baseten, `meta-models/muse-spark-open-1026`, via Chat Completions |
| Reproducibility check | Modal Sandbox |
| Provenance | JSON files (no database) |

Env vars:
- `META_API_KEY`, `META_API_BASE_URL=https://api.meta.ai/v1`
- `META_MODEL` (`muse-spark-1.3-contributor` during development, `muse-spark-1.3` for the demo/deploy)
- `META_VERIFIER_MODEL=muse-wonder-1.0`
- `BASETEN_API_KEY`, `BASETEN_BASE_URL=https://inference.baseten.co/v1`, `BASETEN_MODEL=meta-models/muse-spark-open-1026`
- `MODEL_PROVIDER=meta|baseten`

### LLM wrapper (`app/agent/llm.py`)
- There is **one** wrapper with one interface, `run_turn(messages, tools, response_schema) -> {tool_calls | final}`. The rest of the app never touches a provider SDK directly.
- **Meta path:** `client.responses.create(...)` with `include=["reasoning.encrypted_content"]`, replaying prior reasoning items each turn so the model keeps its train of thought across a multi-step tool loop (Meta's docs: without this the model "reasons from scratch each turn and can lose the thread").
- **Baseten path:** `client.chat.completions.create(...)` with standard tool calling. Same interface, same tools.
- Pass `api_key` explicitly. The OpenAI SDK does not read our env var names on its own.
- Retry 429s with exponential backoff + jitter, honoring `Retry-After`.
- Log `usage` (input, output, cached tokens) per call to provenance, so we can show the cost per investigation.

### Cost plan ($150 Meta credits)
- **Development uses `muse-spark-1.3-contributor`** ($0.10 in / $0.20 out per 1M tokens, ~12–20× cheaper). This is acceptable because the AdK dataset and the figures are public; Contributor mode lets Meta train on content. Never send non-public data.
- **Demo and deploy use `muse-spark-1.3` (Standard mode).**
- The verifier runs on Muse Wonder ($4 / $20 per 1M); keep its input to the tool JSON + interpretation + one PNG.
- Put the stable system prompt and tool schemas first in every request so prompt caching applies.

## 7. Tool registry (the only things the agent can call)

Every tool returns `{ result, params, units, provenance_id }`.

| Tool | Purpose |
|---|---|
| `get_metadata` | atoms, residues, segments, n_frames, dt_ns, has_box, groups |
| `list_groups` | named selections + residue ranges |
| `calculate_contacts` | residue-pair contact frequency matrix for two groups over a frame window |
| `compare_intervals` | per-pair frequency change between two windows, top-N changed, **with block-bootstrap CI** |
| `distance_over_time` | min heavy-atom distance per frame for one residue pair |
| `contact_statistics` | for one pair: autocorrelation time τ, effective sample size, block-bootstrap CI of the contact frequency |
| `cutoff_sensitivity` | contact frequency per pair across several cutoffs (stretch) |
| `highlight` | UI-only: residues + frames to highlight |

**Contact definition (fixed, shown on every PlanCard):** two residues are in contact in a frame if their minimum heavy-atom distance ≤ cutoff (Å).

## 8. Science implementation

- **Precompute once.** Build `min_dist[frame, resA, resB]` for the chosen groups (heavy atoms) and cache it to `.npy`. Most tools then become NumPy slices of it. Use `MDAnalysis.lib.distances.distance_array` with an atom→residue index map.
- **PBC.** If a box exists, use minimum-image distances and record that in provenance. If not, record `pbc: "none"`.
- **Canonical residue key:** `segid:resid:resname`. The backend, Plotly labels, and NGL selections all use this exact key. This is the #1 bug risk, so test it.
- **Time.** Always keep both frame index and `time_ns`.

### 8a. Statistics module (`app/science/stats.py`)

- **Integrated autocorrelation time τ** of the per-frame contact indicator series (0/1). Use an automatic windowing cutoff, and handle constant series (all 0 or all 1) explicitly.
- **Effective sample size:** `N_eff ≈ N / (2τ)`, reported on every result.
- **Moving-block bootstrap** for the contact frequency and for the frequency difference between intervals. Use block length ≈ max(1, ceil(2τ)) and 2,000 resamples, with a fixed seed recorded in provenance.
- **Decision rule.** A change is "supported" only if its 95% CI excludes 0. Otherwise it is "not distinguishable from noise at this sampling."
- **Validation tests:**
  - On a synthetic AR(1) series with a known τ, the estimated τ is within tolerance.
  - On white noise, the block bootstrap CI ≈ the naive CI.
  - On a strongly correlated series, the block CI is clearly wider than the naive CI.

## 9. Agent flow

**What makes this an agent, not a form:** one question becomes a multi-step *investigation* that the agent plans, runs, and adapts. It is not one question → one tool call.

1. **Input.** Either a natural-language question, or a figure image + methods PDF ("reproduce this"). Muse Spark accepts **images and PDFs natively**, so send both directly; no text extraction is needed.
2. **Plan an investigation.** Muse Spark returns a structured plan with **up to 5 steps**. Each step has a tool, params, and a one-line rationale, and later steps can reference earlier results (e.g. step 3 = `contact_statistics` on "the top 3 supported pairs from step 1"). For example, "Why do LID and NMP separate?" might become:
   1. `compare_intervals`
   2. `contact_statistics` on the top supported pairs
   3. `distance_over_time` on the strongest pair
   4. summarize

   In reproduce mode, the agent first returns an **extracted spec** listing each field it read from the figure/methods, with a confidence level and the source ("from caption", "from methods", "assumed").
3. **Validate.** The backend checks the params against the schema and the metadata (groups exist, frames in range, cutoff between 2 and 10 Å).
4. **Confirm once.** The PlanCard shows every step with editable params. The user approves the whole investigation with one Run click.
5. **Execute autonomously.** The agent runs the steps in a tool loop, resolving references to earlier results as it goes. It may **adapt**: drop a step whose input is empty (e.g. no supported changes), or add at most one extra step from the approved tool set. Any adaptation is shown in a live step log ("Step 2 skipped: no pairs passed the CI test"). Parameter changes that the user didn't approve (groups, cutoff) are not allowed mid-run; the agent must stop and propose a new plan instead. Every call is logged to provenance.
6. **Interpret.** Structured output with these sections: Observed (cite numbers from the result), Statistical support (N_eff, CI, supported or not), Possible explanations (hedged), Caveats, Suggested follow-ups (each one a runnable tool call).
7. **Verify.** An independent verifier on **Muse Wonder** (a different model from the one that wrote the interpretation) receives the tool JSON, the interpretation, and the rendered plot PNG. It flags:
   - any number not present in the result,
   - any "significant/changed" claim without CI support,
   - any mismatch between the claim and the plot.

   Flags show in the UI. Run a deterministic number check too, not only the LLM check.
8. **Follow-ups** reuse context. "Does that persist?" resolves to the last selected or top-ranked pair.

Fallback only: if native PDF input fails for a specific file, extract the text with `pypdf` and send text + image.

### 9a. Stretch uses of Meta tooling (only if ahead; in this order)
1. **SAM 3.1 panel finder.** Real paper figures have several panels. Send the figure to `sam-3.1` with a prompt like "heatmap" or "contact map" and crop the matching panel before extraction. Cost: about $0.0025 per image.
2. **Search grounding for literature context.** In the interpretation's "Possible explanations" section, the agent can call the `web_search` tool to cite published work on the AdK LID/NMP motion. Citations show as links, and they are clearly separated from computed results. Cost: $2.50 per 1,000 searches.

## 10. UI

- **Heatmap** (residue × residue frequency). Clicking a cell highlights both residues in NGL and suggests "distance over time for A↔B".
- **Distance plot.** Includes a cutoff line. Clicking a point jumps NGL to that frame.
- **Comparison bar chart** of top changed pairs, with CI error bars. Unsupported changes are greyed out.
- **Reproduce view.** The paper figure is on the left and our figure on the right, with the extracted spec table underneath.
- **Verifier badge** on every result: ✅ verified, or ⚠️ with the list of flags.
- **Model toggle:** Muse Spark (hosted) ↔ Muse Spark Open (Baseten).
- One shared highlight store; the plots and the viewer both subscribe to it.

## 11. Provenance and export

- Each tool call is appended to `investigations/<id>.json`. Each entry records:
  - tool, params, units, and seed
  - dataset file hashes
  - library versions
  - model + provider
  - timestamp
  - a result summary
- **Export** creates `reproduce_<id>.py`: plain MDAnalysis/NumPy that recomputes every result, including bootstrap CIs with the same seed, and saves the plots.
- **"Verify in clean environment"** button: runs the script in a Modal Sandbox and shows whether the numbers match the app (within tolerance).

## 12. Repo layout

```
/backend
  app/main.py
  app/science/   loader.py contacts.py distances.py stats.py
  app/agent/     llm.py tools.py orchestrator.py prompts.py verifier.py
  app/provenance/ record.py export.py
  tests/
  modal_app.py   (deploy entrypoint)
/frontend
  src/components/ Chat PlanCard Heatmap DistancePlot CompareChart ReproduceView Viewer VerifierBadge ModelToggle
  src/state/
/config/dataset.yaml
/assets/figures/  (open-access figures + ATTRIBUTION.md)
README.md  CLAUDE.md  SPEC.md
```

## 13. Daily plan (one day = one Claude Code session)

| Day | Date | Build | Done when |
|---|---|---|---|
| 1 | Tue Oct 27 | Repo scaffold, dataset load, metadata, groups, min-distance precompute, `calculate_contacts`, `distance_over_time` | pytest passes; results match a hand-written MDAnalysis check on 3 pairs |
| 2 | Wed Oct 28 | Frontend scaffold, NGL viewer, Plotly heatmap, click→highlight | clicking 5 different cells highlights the correct residues (checked by eye) |
| 3 | Thu Oct 29 | `stats.py` (τ, N_eff, block bootstrap), `compare_intervals`, `contact_statistics`, comparison chart with CIs | synthetic AR(1) + white-noise tests pass |
| 4 | Fri Oct 30 | LLM wrapper (Responses API + reasoning replay), multi-step investigation plan, PlanCard confirm-once, autonomous tool loop with live step log, interpretation | one question → 3-step plan → edit → run → step log → interpretation, end to end |
| 5 | Sat Oct 31 | Reproduce-this-figure (image + methods PDF sent natively → extracted spec → plan → side-by-side) | the real open-access figure produces a matching analysis |
| 6 | Sun Nov 1 | Verifier on Muse Wonder (deterministic number check + plot/claim check), context-aware follow-ups | a deliberately wrong interpretation gets flagged |
| 7 | Mon Nov 2 | Export script, Modal deploy, Modal Sandbox verify, Baseten toggle | live URL works; the sandbox run reproduces the numbers |
| 8 | Tue Nov 3 | Freeze. README, architecture diagram, 3-minute video, **submit** | submission confirmed |
| 9 | Wed Nov 4 | Buffer, bug fixes only, X post | — |

**Cut order if behind:** stretch items in §9a → `cutoff_sensitivity` → agent adaptation in step 5 (keep the multi-step plan) → Baseten toggle → Modal Sandbox button (keep the export itself) → LLM plot check in the verifier (keep the deterministic check).
**Never cut:** linked viewer, statistics, reproduce-this-figure, export.

## 14. Demo video (3 minutes)

1. **0:00–0:20.** Drop in a paper figure. The extracted spec appears, then the side-by-side reproduction.
2. **0:20–1:00.** Ask "why do the LID and NMP domains come apart?" The agent proposes a 3–4 step investigation; edit the cutoff and approve once. The step log runs live (including one adaptive skip or add). The bar chart appears with CIs, and some changes are greyed out as "noise."
3. **1:00–1:40.** Click a supported pair: it highlights in 3D. Ask "does that persist?" The distance plot appears; click the crossing point and the viewer jumps to that frame.
4. **1:40–2:10.** The verifier catches an overclaim (pre-scripted question). Show N_eff ("1,000 frames ≈ 40 independent samples").
5. **2:10–2:40.** Export the script, verify it in the Modal Sandbox, and show that the numbers match.
6. **2:40–3:00.** Flip to open-weight mode ("labs can self-host"), plus the built-during-hackathon summary.

## 15. Out of scope

- Running simulations
- Trajectory uploads
- Formats beyond the verified dataset
- RMSD/RMSF
- Accounts
- A database
- Arbitrary code execution
- Lab data
- The C++ kernel (post-hackathon)