"""Modal deployment of the FastAPI backend (SPEC §12).

Layout inside the container (app/config.py finds the repo root two levels above app/):
  /root/app      backend package
  /config        dataset.yaml
  /cache         Modal Volume: downloaded dataset + min-distance caches (persist across containers)

Deploy:  cd backend && modal deploy modal_app.py
Warm:    cd backend && modal run modal_app.py::warm   (downloads the dataset, fills the caches once)
CORS:    set ALLOWED_ORIGINS (comma-separated, e.g. the Vercel URL) in your shell before deploying.
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
app = modal.App("trajectorygpt", image=image)
FUNCTION_KW = dict(
    volumes={"/cache": volume},
    memory=2048,
    timeout=600,
    env={"ALLOWED_ORIGINS": os.environ.get("ALLOWED_ORIGINS", "")},
)


@app.function(**FUNCTION_KW)
@modal.asgi_app(label="trajectorygpt-api")
def api():
    from app.main import app as fastapi_app
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
