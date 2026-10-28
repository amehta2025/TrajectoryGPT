# TrajectoryGPT — Build Spec (v2)

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
| LLM (default) | Meta Model API, Muse Spark (OpenAI-compatible SDK) |
| LLM (open-weight mode) | Baseten, `meta-models/muse-spark-open-1026` |
| Reproducibility check | Modal Sandbox |
| Provenance | JSON files (no database) |

Env vars:
- `META_API_KEY`, `META_API_BASE_URL`, `META_MODEL`
- `BASETEN_API_KEY`, `BASETEN_BASE_URL`, `BASETEN_MODEL`
- `MODEL_PROVIDER=meta|baseten`

There is **one** LLM client wrapper in `app/agent/llm.py`. Switching providers changes only the base URL, key, and model.

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

1. **Input.** Either a natural-language question, or a figure image + methods text ("reproduce this").
2. **Plan.** Muse Spark returns a structured plan (tool + params + one-line rationale). In reproduce mode, it first returns an **extracted spec** listing each field it read from the figure/methods, with a confidence level and the source ("from caption", "from methods", "assumed").
3. **Validate.** The backend checks the params against the schema and the metadata (groups exist, frames in range, cutoff between 2 and 10 Å).
4. **Confirm.** The PlanCard shows editable params; the Run button is required.
5. **Execute.** Run the tool and log it to provenance.
6. **Interpret.** Structured output with these sections: Observed (cite numbers from the result), Statistical support (N_eff, CI, supported or not), Possible explanations (hedged), Caveats, Suggested follow-ups (each one a runnable tool call).
7. **Verify.** A second Muse Spark pass receives the tool JSON, the interpretation, and the rendered plot PNG. It flags:
   - any number not present in the result,
   - any "significant/changed" claim without CI support,
   - any mismatch between the claim and the plot.

   Flags show in the UI. Run a deterministic number check too, not only the LLM check.
8. **Follow-ups** reuse context. "Does that persist?" resolves to the last selected or top-ranked pair.

If the Meta API does not accept a PDF in the same call as the image, extract the text with `pypdf` and send text + image. Verify this at Day 4 office hours.

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
| 4 | Fri Oct 30 | LLM wrapper, tool calling, PlanCard confirm flow, interpretation | NL question → plan → edit → run → interpretation, end to end |
| 5 | Sat Oct 31 | Reproduce-this-figure (image + methods → extracted spec → plan → side-by-side) | the real open-access figure produces a matching analysis |
| 6 | Sun Nov 1 | Verifier (deterministic number check + LLM plot/claim check), context-aware follow-ups | a deliberately wrong interpretation gets flagged |
| 7 | Mon Nov 2 | Export script, Modal deploy, Modal Sandbox verify, Baseten toggle | live URL works; the sandbox run reproduces the numbers |
| 8 | Tue Nov 3 | Freeze. README, architecture diagram, 3-minute video, **submit** | submission confirmed |
| 9 | Wed Nov 4 | Buffer, bug fixes only, X post | — |

**Cut order if behind:** `cutoff_sensitivity` → Baseten toggle → Modal Sandbox button (keep the export itself) → LLM plot check in the verifier (keep the deterministic check).
**Never cut:** linked viewer, statistics, reproduce-this-figure, export.

## 14. Demo video (3 minutes)

1. **0:00–0:20.** Drop in a paper figure. The extracted spec appears, then the side-by-side reproduction.
2. **0:20–1:00.** Ask "which contacts change most between the halves?" Show the PlanCard and edit the cutoff. The bar chart appears with CIs, and some changes are greyed out as "noise."
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
