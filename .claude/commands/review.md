You are a REVIEWER, not a builder. Do not edit, create, or delete any files. Do not commit.

1. Read SPEC.md and CLAUDE.md.
2. Look at today's changes: `git diff` (uncommitted) and `git log --since=midnight -p` (committed today).
3. Check them against the spec and the hard rules. Look for:
   - Bugs and wrong science (units, off-by-one frames, wrong residue mapping, stats mistakes)
   - Violations of hard rules (LLM producing numbers, secrets, eval of model output, stats on stats_valid:false data)
   - Missing or weak tests (tests that would pass even if the code were wrong)
   - Scope creep (things not in today's SPEC §14 row)
4. Run the fast tests: `cd backend && pytest -q -m "not slow"`.
5. Report a numbered list, most serious first. For each: file and line, what's wrong, why it matters, suggested fix. Mark each as MUST FIX / SHOULD FIX / NIT. If nothing is wrong, say so plainly. Don't invent problems.
