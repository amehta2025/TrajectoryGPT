# CLAUDE.md

Project: **TrajectoryGPT**, an MD research copilot for the Meta Global AI Developer Hackathon. Full spec: @SPEC.md

## About me
When you build something, explain it in 3–5 bullets at the end of each task: what the file does, and why it's built that way. Don't assume background.

## How we work
- Build only the current day's scope from SPEC.md §13. Don't start future days or add unrequested features.
- Before writing code for a new feature, give a short plan (files to touch, approach) and wait for my OK.
- Make small, focused changes. Prefer editing existing files over creating new ones. Don't create extra .md files.
- After each feature: run the relevant tests, show me the result, then commit with a clear message.
- If something in SPEC.md seems wrong or impossible, say so instead of working around it silently.

## Hard rules
- The LLM never invents numbers. All values come from functions in `backend/app/science/`.
- Science functions are pure Python (no FastAPI imports) and have pytest tests.
- Residue key everywhere: `segid:resid:resname`.
- Units: Å and ns, with frame index kept alongside time.
- Never commit secrets. Keys live in `.env` (gitignored); `.env.example` lists the names.
- No `eval`/exec of model output. The agent only calls the registered tools.

## Commands
- Backend: `cd backend && uvicorn app.main:app --reload`
- Tests: `cd backend && pytest -q`
- Frontend: `cd frontend && npm run dev`
