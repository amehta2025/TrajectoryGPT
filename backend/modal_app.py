"""Modal deployment of the FastAPI backend (SPEC §12).

Layout inside the container (app/config.py finds the repo root two levels above app/):
  /root/app      backend package
  /config        dataset.yaml
  /cache         Modal Volume: downloaded dataset + min-distance caches (persist across containers)

Deploy:  cd backend && modal deploy modal_app.py
Warm:    cd backend && modal run modal_app.py::warm   (downloads the dataset, fills the caches once)
Export:  cd backend && modal run modal_app.py::verify_export --run-id <id>
         (runs reproduce_<id>.py in a clean Modal Sandbox; the run is read locally or from the Volume)
CORS:    set ALLOWED_ORIGINS (comma-separated, e.g. the Vercel URL) in your shell before deploying.
Replay:  set APP_MODE=replay before deploying to turn the LLM off (kill switch, SPEC §12); unset = live.
Secrets: trajectorygpt-meta (META_API_KEY) and trajectorygpt-baseten (BASETEN_API_KEY) must exist in Modal;
         trajectorygpt-passcode (DEMO_PASSCODE) too when deploying with PASSCODE=1 (the passcode is off by default).
         The deploy always uses the Standard model (users upload figures; Contributor mode trains on inputs).
"""
import os
from pathlib import Path

import modal

HERE = Path(__file__).resolve().parent
IGNORE = ["**/__pycache__/**", "**/*.pyc"]

volume = modal.Volume.from_name("trajectorygpt-cache", create_if_missing=True)
plan_claims = modal.Dict.from_name("trajectorygpt-plan-claims", create_if_missing=True)  # plan_id -> run_id
image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install_from_requirements(str(HERE / "requirements.txt"))
    .add_local_dir(HERE / "app", "/root/app", ignore=IGNORE)
    .add_local_dir(HERE.parent / "config", "/config")
    .add_local_dir(HERE / "replay", "/backend/replay")  # recorded runs for replay mode (app/safety/replay.py)
)
# The demo passcode is off (open for judging, 2026-11-03); the $10 daily budget and per-IP rate limits still
# apply. Turn it back on with PASSCODE=1 modal deploy modal_app.py (uses the trajectorygpt-passcode secret).
PASSCODE = os.environ.get("PASSCODE") == "1"
SECRETS = [modal.Secret.from_name("trajectorygpt-meta"), modal.Secret.from_name("trajectorygpt-baseten")]  # BASETEN_API_KEY: open-weight toggle
if PASSCODE:
    SECRETS.append(modal.Secret.from_name("trajectorygpt-passcode"))
app = modal.App("trajectorygpt", image=image)
FUNCTION_KW = dict(
    volumes={"/cache": volume},
    memory=2048,
    timeout=600,
    env={"ALLOWED_ORIGINS": os.environ.get("ALLOWED_ORIGINS", "")},
)
API_ENV = {
    **FUNCTION_KW["env"],
    "MODEL_PROVIDER": "meta",
    "META_MODEL": "muse-spark-1.3",  # Standard mode on every deploy (SPEC §6b)
    "META_API_BASE_URL": "https://api.meta.ai/v1",
    "BASETEN_BASE_URL": "https://inference.baseten.co/v1",
    "BASETEN_MODEL": "meta-models/muse-spark-open-1026",  # open-weight toggle; Meta stays the default
    "DAILY_LLM_BUDGET_USD": os.environ.get("DAILY_LLM_BUDGET_USD", "10"),
    "REQUIRE_PASSCODE": "1" if PASSCODE else "0",  # with PASSCODE=1: fail closed if the passcode secret is empty
    "APP_MODE": os.environ.get("APP_MODE", "live"),  # kill switch: APP_MODE=replay modal deploy modal_app.py
    "INVESTIGATIONS_DIR": "/cache/investigations",  # runs persist on the Volume (replay curation, restarts)
    "SANDBOX_ENABLED": "1",  # the report's "check in a clean sandbox" button (app/provenance/sandbox.py)
}


# One container: plans and running investigations live in its memory, and the live step log (SSE)
# must reach the same container, so it serves many requests concurrently instead of scaling out.
# WARM=1 keeps the one container running, so nobody waits for a cold start (about 6 s). For judging
# (Nov 5-8): WARM=1 ALLOWED_ORIGINS=... modal deploy modal_app.py; redeploy without WARM to stop paying for it.
WARM = os.environ.get("WARM") == "1"


@app.function(**{**FUNCTION_KW, "env": API_ENV}, secrets=SECRETS, max_containers=1, min_containers=1 if WARM else 0)
@modal.concurrent(max_inputs=32)
@modal.asgi_app(label="trajectorygpt-api")
def api():
    from app.agent import session
    from app.main import app as fastapi_app
    session.ON_RUN_FINISHED = volume.commit  # make a finished run durable right away
    session.ON_PLAN_SAVED = volume.commit  # a plan outlives this container (scale-down, redeploy)
    session.RELOAD_STORE = volume.reload  # read a plan another container saved

    class SharedClaims(session.FileClaims):
        """One plan, one run across containers: modal.Dict's put(skip_if_exists=True) is atomic for every
        container, which files on the Volume are not. The claim is also written to the Volume as a record."""

        def get(self, plan_id):
            return plan_claims.get(plan_id) or super().get(plan_id)

        def claim(self, plan_id, run_id):
            winner = run_id if plan_claims.put(plan_id, run_id, skip_if_exists=True) else plan_claims.get(plan_id, run_id)
            super().claim(plan_id, winner)
            return winner

    session.CLAIMS = SharedClaims()
    return fastapi_app


@app.function(**FUNCTION_KW)
def warm():
    """Fetch adk_equilibrium into the Volume and precompute the three domain pairs."""
    from app.science.contacts import precompute_min_distances
    for ds in ("adk_equilibrium", "adk_transition"):
        for a, b in (("NMP", "CORE"), ("LID", "CORE"), ("LID", "NMP")):
            md = precompute_min_distances(ds, a, b, progress=print)
            print(ds, a, b, md.min_dist.shape, "computed" if md.computed else "cached")
    volume.commit()


@app.local_entrypoint()
def verify_export(run_id: str):
    """Generate reproduce_<run_id>.py here and run it in a clean Sandbox against the Volume's dataset."""
    import json

    from app.provenance import export, sandbox
    local = HERE.parent / "investigations" / f"{run_id}.json"
    if local.exists():
        run = json.loads(local.read_text(encoding="utf-8"))
    else:
        run = json.loads(b"".join(volume.read_file(f"investigations/{run_id}.json")))
    script = export.build_script(run)
    print(f"{export.script_name(run_id)}: {len(script):,} characters; starting a clean sandbox...")
    out = sandbox.run_script(script, run_id)
    print(out["output"])
    print(f"sandbox {out['sandbox_id']}: exit {out['returncode']}, {out['wall_s']} s, "
          f"{out['n_match']} of {out['n_values']} values match -> {'REPRODUCED' if out['reproduced'] else 'NOT REPRODUCED'}")
