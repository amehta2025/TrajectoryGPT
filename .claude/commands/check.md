End-of-session check. Do not add features.

1. Run `cd backend && pytest -q` (and `cd frontend && npm run build` if the frontend exists). Report pass/fail.
2. Run `git status` and confirm that no secrets (`.env`, API keys) are staged or committed.
3. Compare what exists against today's "done when" check in SPEC.md §14. Say plainly: done, or not done and what's missing.
4. If everything passes, commit any remaining work with a clear message and push.
5. Write a 3-line summary I can paste into my notes: what works, what's broken, what tomorrow starts with.
