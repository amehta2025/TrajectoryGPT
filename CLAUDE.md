# CLAUDE.md

Project: **TrajectoryGPT**, an MD research copilot for the Meta Global AI Developer Hackathon. Full spec: @SPEC.md

## About me
- I'm a beginner at this stack. At the end of each task, explain what you built in 3–5 plain-English bullets: what each file does and why it's built that way. Don't assume background.
- I'm on **Windows**. Use the project venv (`.venv`). Give commands that work in my terminal, and tell me which terminal they're for.

## How we work
- Build only the current day's scope from SPEC.md §14. Don't start future days or add unrequested features.
- Before writing code for a new feature, give a short plan (files to touch, approach, how you'll test it) and wait for my OK.
- Make small, focused changes. Prefer editing existing files over creating new ones. The only extra .md file allowed is `notes/recon.md`.
- Ask before installing any new package, and add every package to `backend/requirements.txt` (pinned).
- After each feature: run the relevant tests, show me the result, then commit with a clear message.
- If something in SPEC.md seems wrong, impossible, or scientifically shaky, say so instead of working around it silently.
- If you're unsure of an external API's exact request shape, read `docs_local/meta/` first. If the page isn't there, stop and ask me to paste it. Never guess.

## Hard rules
- The LLM never writes numbers. It writes placeholders that code fills from stored tool results.
- Science functions are pure Python (no FastAPI imports) and have pytest tests. Fast tests use the `adk_transition` dataset; slow ones are marked `@pytest.mark.slow`.
- Statistics are refused on datasets with `stats_valid: false`.
- Residue key everywhere: `dataset:segid:resid:resname`, built and parsed by one function each.
- Units: Å, and ns only where real time exists. Keep the frame index alongside.
- Tool results sent to the LLM are short summaries (<~2k tokens). Full data goes to the UI.
- Never commit secrets, `.env`, trajectories, `cache/`, `investigations/`, or `docs_local/`. Keys live in `.env`; `.env.example` lists the names.
- No `eval`/`exec` of model output. The agent only calls registered tools. The export script comes from our templates.
- Never print or log API keys or `.env` values.

## Meta Model API facts (from Meta's docs, Oct 2026)
- Base URL `https://api.meta.ai/v1`. Agent model `muse-spark-1.3` (dev: `muse-spark-1.3-contributor`). Verifier `muse-wonder-1.0`.
- Use the OpenAI Python SDK's **Responses API** (`client.responses.create`) with `include=["reasoning.encrypted_content"]`, and pass the output items back in the next turn's input. Read text via `response.output_text`.
- Images and PDFs are accepted natively. Always pass `api_key` explicitly.
- `store=False` on every Meta call. Always set reasoning effort explicitly (never `none`; Baseten defaults to `xhigh`).
- Production and every deploy use `muse-spark-1.3` (Standard): users upload figures, and Contributor mode trains on inputs. `-contributor` is dev only.
- Retries are ours only (SDK `max_retries=0`): retry 429/500/503 honoring `Retry-After`, max 3 tries. Never retry 504 (stream or lower effort instead) or 400–413.
- Verifier = one Muse Wonder call per report, cost logged separately. Model prices live in one config table.
- Baseten: `https://inference.baseten.co/v1`, model `meta-models/muse-spark-open-1026` (hackathon guide; keep it), Chat Completions. If `-1026` fails in the smoke test, try `meta-models/muse-spark-open` and tell me.
- Speech to text (stretch): `muse-voice-transcribe-1.0`, `POST /v1/asr/transcribe`, mono 16-bit PCM WAV at 16 or 24 kHz.

## Commands (run from the repo root unless noted)
- Activate venv: PowerShell `.venv\Scripts\Activate.ps1` · Git Bash `source .venv/Scripts/activate`
- Tests (fast): `cd backend && pytest -q -m "not slow"` · all: `pytest -q`
- Backend: `cd backend && uvicorn app.main:app --reload`
- Agent CLI: `cd backend && python -m app.agent.cli run --turns 3 "<question>"`
- Frontend: `cd frontend && npm run dev`