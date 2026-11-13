"""Behavioral coverage for the local wake/sleep runtime; no network or model calls."""
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from activersi import runtime as rt
import active_rsi as gate


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.workspace = self.base / "workspace"

    def cli(self, *args, ok=True, workspace=None):
        run = subprocess.run(
            [sys.executable, "-m", "activersi", "--workspace", str(workspace or self.workspace), *args],
            cwd=ROOT, text=True, capture_output=True, timeout=8,
        )
        self.assertEqual(run.returncode, 0 if ok else 2, run.stdout + run.stderr)
        return json.loads(run.stdout)

    def init_observation(self, command=None, timeout=90):
        args = ["init", "--timeout", str(timeout)]
        if command:
            args += ["--adapter-command", json.dumps(command)]
        self.cli(*args)
        self.cli("observe", "--kind", "correction", "--text", "I cannot review this plan comfortably on my phone.")

    def adapter(self, body):
        path = self.base / "adapter.py"
        path.write_text(body)
        return [sys.executable, str(path)]

    def valid_adapter(self, capture):
        return self.adapter("""import json, sys
from pathlib import Path
request = json.load(sys.stdin)
Path(sys.argv[1]).write_text(json.dumps(request))
proposal = request['response_contract']
proposal['hypotheses'][0]['observation_ids'] = [request['observations'][-1]['id']]
proposal['candidate_harness'] = request['harness'] + '\\nVerify the target display before proposing a layout.\\n'
proposal['candidate_policy'] = {'question_budget': 0, 'interruption_weight': 2.0, 'minimum_value': 0.5}
print(json.dumps(proposal))
""") + [str(capture)]

    def test_cli_demo_cycle_feedback_stays_synthetic_and_preserves_harness(self):
        self.init_observation()
        original = (self.workspace / "harness/AGENT.md").read_bytes()
        cycle = self.cli("dream", "--demo")
        hypothesis = cycle["hypotheses"][0]
        self.assertTrue(hypothesis["synthetic"])
        self.assertEqual(hypothesis["status"], "unconfirmed")
        wake = self.cli("wake")
        self.assertEqual(wake["action"], "probe")
        self.assertTrue(wake["probes"][0]["synthetic"])
        self.assertEqual(self.cli("wake")["action"], "continue")
        result = self.cli("feedback", "--hypothesis", hypothesis["id"], "--decision", "accept", "--text", "Synthetic walkthrough reply")
        self.assertEqual(result["hypothesis_status"], "synthetic_confirmed")
        self.assertFalse(result["harness_changed"])
        self.assertEqual((self.workspace / "harness/AGENT.md").read_bytes(), original)

    def test_existing_workspace_is_preserved_and_other_workspace_isolated(self):
        self.init_observation()
        before = (self.workspace / "state.json").read_bytes()
        self.cli("init", ok=False)
        other = self.base / "another"
        self.cli("init", workspace=other)
        self.assertEqual(self.cli("status", workspace=other)["observations"], 0)
        self.assertEqual((self.workspace / "state.json").read_bytes(), before)
        self.assertEqual(stat.S_IMODE(self.workspace.stat().st_mode), 0o700)
        for path in self.workspace.rglob("*"):
            if path.is_file():
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600, str(path))

    def test_invalid_adapter_does_not_commit_state_or_cycle(self):
        command = self.adapter("print('{\"hypotheses\": []}')\n")
        self.init_observation(command)
        before = (self.workspace / "state.json").read_bytes()
        result = self.cli("dream", ok=False)
        self.assertIn("hypotheses", result["reason"])
        self.assertEqual((self.workspace / "state.json").read_bytes(), before)
        self.assertFalse((self.workspace / "cycles").exists())
        self.assertFalse((self.workspace / ".lock").exists())

    def test_adapter_timeout_preserves_state_and_releases_lock(self):
        command = self.adapter("import time\ntime.sleep(20)\n")
        self.init_observation(command, timeout=1)
        before = (self.workspace / "state.json").read_bytes()
        self.assertIn("timed out", self.cli("dream", ok=False)["reason"])
        self.assertEqual((self.workspace / "state.json").read_bytes(), before)
        self.assertFalse((self.workspace / ".lock").exists())
        self.assertEqual(self.cli("status")["observations"], 1)

    def test_rejected_hypothesis_not_reoffered_without_new_observation(self):
        self.init_observation()
        first = self.cli("dream", "--demo")
        hid = first["hypotheses"][0]["id"]
        self.cli("feedback", "--hypothesis", hid, "--decision", "reject", "--text", "This explanation does not fit.")
        self.cli("dream", "--demo")
        self.assertEqual(self.cli("wake")["action"], "continue")

    def test_next_dream_and_wake_load_promoted_policy_from_harness(self):
        capture = self.base / "request.json"
        self.init_observation(self.valid_adapter(capture))
        cycle = self.cli("dream")
        candidate = Path(cycle["cycle"]["candidate"])
        baseline = self.workspace / "harness/AGENT.md"
        # This report is a test fixture for version activation, not empirical results.
        report = self.base / "fixture-report.json"
        report.write_text(json.dumps({
            "baseline_sha256": gate.digest(baseline.read_bytes()),
            "candidate_sha256": gate.digest(candidate.read_bytes()),
            "evaluator": "unit-test-fixture", "risk": "low", "cases": [
                {"id": str(i), "split": "heldout", "baseline_pass": i != 0,
                 "candidate_pass": True, "safety_violation": False} for i in range(3)]
        }))
        result = gate.promote(self.workspace, candidate, report, False)
        self.assertEqual(result["status"], "promoted")
        self.assertEqual(self.cli("wake")["action"], "continue")
        self.cli("dream")
        request = json.loads(capture.read_text())
        self.assertEqual(request["discovery_policy"], {"question_budget": 0, "interruption_weight": 2.0, "minimum_value": 0.5})
        self.assertIn("Verify the target display", request["harness"])
        self.assertEqual(gate.rollback(self.workspace, result["revision"])["status"], "rolled_back")
        self.assertEqual(rt.harness_policy(baseline.read_text()), rt.DEFAULT_POLICY)

    def test_real_dream_excludes_synthetic_hypotheses_and_feedback(self):
        capture = self.base / "request.json"
        self.init_observation(self.valid_adapter(capture))
        cycle = self.cli("dream", "--demo")
        self.cli("feedback", "--hypothesis", cycle["hypotheses"][0]["id"],
                 "--decision", "accept", "--text", "Synthetic acceptance only")
        self.cli("dream")
        request = json.loads(capture.read_text())
        self.assertEqual(request["previous_hypotheses"], [])
        self.assertEqual(request["feedback"], [])
        self.assertEqual(len(request["observations"]), 1)

    def test_proposal_rejects_unknown_observation_and_nonfinite_scores(self):
        request = {"observations": [{"id": "known"}], "harness": "baseline"}
        proposal = rt.demo_proposal(request)
        proposal["hypotheses"][0]["observation_ids"] = ["invented"]
        with self.assertRaisesRegex(rt.RuntimeError, "existing observation"):
            rt.validate_proposal(proposal, {"known"})
        proposal = rt.demo_proposal(request)
        proposal["hypotheses"][0]["probe"]["information_gain"] = float("nan")
        with self.assertRaisesRegex(rt.RuntimeError, "finite"):
            rt.validate_proposal(proposal, {"known"})

    def test_unknown_feedback_does_not_mutate_state(self):
        self.init_observation()
        before = (self.workspace / "state.json").read_bytes()
        self.cli("feedback", "--hypothesis", "missing", "--decision", "accept", "--text", "actual reply", ok=False)
        self.assertEqual((self.workspace / "state.json").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
