"""Copy saved runs into the replay set (backend/replay/), for replay mode (SPEC §12).

    cd backend
    python -m scripts.curate_replay <run_id> --label "Which LID–CORE contacts change?"

Only real runs that ended with a verified or flagged report are accepted. The copy drops the model's raw
input history (with its encrypted reasoning); figures the run used are copied alongside.
"""
import argparse
import json
import sys

from app.agent.figure import FIGURES_DIR
from app.agent.orchestrator import INVESTIGATIONS_DIR
from app.safety import replay


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m scripts.curate_replay")
    ap.add_argument("run_id")
    ap.add_argument("--label", help="short button text on the start screen in replay mode")
    a = ap.parse_args(argv)
    run = json.loads((INVESTIGATIONS_DIR / f"{a.run_id}.json").read_text(encoding="utf-8"))
    if run.get("status") not in ("verified", "flagged"):
        print(f"{a.run_id}: status {run.get('status')!r}; only runs with a verified or flagged report are replayed")
        return 1
    if (run.get("plan_usage") or [{}])[0].get("model") != "muse-spark-1.3":
        print(f"{a.run_id}: not recorded on muse-spark-1.3 (the production model)")
        return 1
    path = replay.curate(run, a.label, figures_dir=FIGURES_DIR)
    print(f"{path} ({path.stat().st_size / 1024:.0f} KB): {run['question'][:80]} [{run['status']}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
