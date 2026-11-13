#!/usr/bin/env python3
"""Run a synthetic wake/sleep/evaluate/promote/rollback cycle without a network."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from activersi.runtime import initialize, observe, dream, wake, feedback, status


def command(*args):
    result = subprocess.run([sys.executable, *map(str, args)], cwd=ROOT, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stdout + result.stderr)
    return json.loads(result.stdout)


def run(workspace):
    initialize(workspace)
    observe(workspace, "Synthetic tutorial: the weekly plan is awkward to review on my phone.", "correction")
    cycle = dream(workspace, demo=True)
    probes = wake(workspace)
    feedback(workspace, cycle["hypotheses"][0]["id"], "accept", "Synthetic user: a compact layout helps.")
    report = workspace / "evaluation.json"
    evaluation = command(ROOT / "scripts/evaluate.py", "--root", workspace,
                         "--candidate", cycle["cycle"]["candidate"], "--cases", ROOT / "examples/cases.json",
                         "--command", json.dumps([sys.executable, str(ROOT / "examples/demo_evaluator.py")]),
                         "--output", report, "--risk", "low")
    promotion = command(ROOT / "scripts/active_rsi.py", "--root", workspace, "promote", "--candidate",
                        cycle["cycle"]["candidate"], "--report", report)
    second = dream(workspace, demo=True)
    rollback = command(ROOT / "scripts/active_rsi.py", "--root", workspace, "rollback", "--revision", promotion["revision"])
    print(json.dumps({"synthetic": True, "workspace": str(workspace), "probes": probes,
                      "evaluation": evaluation, "promotion": promotion, "second_cycle": second["cycle"],
                      "rollback": rollback, "final": status(workspace)}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, help="New directory to retain tutorial artifacts")
    args = parser.parse_args()
    if args.workspace:
        run(args.workspace.absolute())
    else:
        with tempfile.TemporaryDirectory(prefix="activersi-demo-") as directory:
            run(Path(directory) / "workspace")
