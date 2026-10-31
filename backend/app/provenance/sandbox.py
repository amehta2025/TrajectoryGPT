"""Run an exported reproduce script in a clean Modal Sandbox (SPEC §11 "verify in clean environment").

The sandbox image has only the science libraries, pinned to the versions installed here, and none of our
code: the script brings its own bundled copy. The dataset comes from the Modal Volume (already downloaded),
mounted at /cache; the script recomputes everything else from the trajectory.

Used by `modal run modal_app.py::verify_export --run-id ...` and by the deployed API's sandbox button
(SANDBOX_ENABLED=1). One check takes about one to three minutes.
"""
from __future__ import annotations

import re
import threading
import time
from importlib.metadata import version

PACKAGES = ["MDAnalysis", "MDAnalysisData", "MDAnalysisTests", "numpy", "scipy", "matplotlib"]
APP_NAME = "trajectorygpt"
VOLUME = "trajectorygpt-cache"
DATA_DIR = "/cache/mdanalysis_data"
TIMEOUT_S = 900

JOBS: dict[str, dict] = {}  # run_id -> {status: running|done|error, returncode, output, matched, started, wall_s}
_lock = threading.Lock()


def image():
    import modal
    return modal.Image.debian_slim(python_version="3.12").pip_install(*[f"{p}=={version(p)}" for p in PACKAGES])


def summarize(output: str, returncode: int | None) -> dict:
    """Pull the verdict and the match count out of the script's output."""
    m = re.search(r"(\d+) of (\d+) stored values match", output)
    return {"reproduced": returncode == 0 and "\nREPRODUCED" in output,
            "n_match": int(m[1]) if m else None, "n_values": int(m[2]) if m else None}


def run_script(script: str, run_id: str) -> dict:
    """Create a sandbox, write the script, run it against the Volume's dataset, return its output."""
    import modal
    t0 = time.perf_counter()
    app = modal.App.lookup(APP_NAME, create_if_missing=True)
    sb = modal.Sandbox.create(image=image(), app=app, volumes={"/cache": modal.Volume.from_name(VOLUME)},
                              timeout=TIMEOUT_S, cpu=2.0, memory=4096, tags={"run_id": run_id})
    try:
        sb.filesystem.write_text(script, "/tmp/reproduce.py")
        p = sb.exec("python", "-I", "/tmp/reproduce.py", "--data-dir", DATA_DIR, "--out", "/tmp/out")
        p.wait()
        output = p.stdout.read() + (p.stderr.read() or "")
        rc = p.returncode
    finally:
        sb.terminate()
    return {"returncode": rc, "output": output, **summarize(output, rc), "wall_s": round(time.perf_counter() - t0, 1),
            "sandbox_id": sb.object_id}


def start(run_id: str, script: str, on_done=None) -> dict:
    """Start one background check per run (a second click while it runs returns the same job)."""
    with _lock:
        job = JOBS.get(run_id)
        if job and job["status"] == "running":
            return job
        job = JOBS[run_id] = {"status": "running", "started": time.time()}

    def work():
        try:
            job.update(run_script(script, run_id), status="done")
        except Exception as e:  # shown to the user; the export itself still works
            job.update(status="error", error=f"{type(e).__name__}: {e}")
        if on_done:
            on_done(run_id, job)

    threading.Thread(target=work, name=f"sandbox-{run_id}", daemon=True).start()
    return job
