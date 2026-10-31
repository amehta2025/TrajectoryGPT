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
Secrets: trajectorygpt-meta (META_API_KEY) and trajectorygpt-passcode (DEMO_PASSCODE) must exist in Modal.
         The deploy always uses the Standard model (users upload figures; Contributor mode trains on inputs).
"""
import os
from pathlib import Path

import modal

HERE = Path(__file__).resolve().parent
IGNORE = ["**/__pycache__/**", "**/*.pyc"]

volume = modal.Volume.from_name("trajectorygpt-cache", create_if_missing=True)
image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install_from_requirements(str(HERE / "requirements.txt"))
    .add_local_dir(HERE / "app", "/root/app", ignore=IGNORE)
    .add_local_dir(HERE.parent / "config", "/config")
)
SECRETS = [modal.Secret.from_name("trajectorygpt-meta"), modal.Secret.from_name("trajectorygpt-passcode")]
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
    "DAILY_LLM_BUDGET_USD": os.environ.get("DAILY_LLM_BUDGET_USD", "3"),
    "REQUIRE_PASSCODE": "1",  # fail closed if the passcode secret is empty
    "APP_MODE": "live",
    "INVESTIGATIONS_DIR": "/cache/investigations",  # runs persist on the Volume (replay curation, restarts)
    "SANDBOX_ENABLED": "1",  # the report's "check in a clean sandbox" button (app/provenance/sandbox.py)
}


# One container: plans and running investigations live in its memory, and the live step log (SSE)
# must reach the same container, so it serves many requests concurrently instead of scaling out.
@app.function(**{**FUNCTION_KW, "env": API_ENV}, secrets=SECRETS, max_containers=1)
@modal.concurrent(max_inputs=32)
@modal.asgi_app(label="trajectorygpt-api")
def api():
    from app.agent import session
    from app.main import app as fastapi_app
    session.ON_RUN_FINISHED = volume.commit  # make a finished run durable right away
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
