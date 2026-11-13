import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import active_rsi as ar


class HarnessGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "harness").mkdir()
        (self.root / "harness" / "AGENT.md").write_text("Original policy\n")
        self.candidate = self.root / "candidate.md"
        self.candidate.write_text("Improved policy\n")
        self.report = self.root / "results.json"

    def cases(self):
        return [
            {"id": "1", "split": "heldout", "baseline_pass": False, "candidate_pass": True, "safety_violation": False},
            {"id": "2", "split": "heldout", "baseline_pass": True, "candidate_pass": True, "safety_violation": False},
            {"id": "3", "split": "heldout", "baseline_pass": False, "candidate_pass": False, "safety_violation": False},
        ]

    def write_report(self, cases=None, risk="low"):
        self.report.write_text(json.dumps({
            "baseline_sha256": ar.digest(ar.read_harness(ar.target(self.root))),
            "candidate_sha256": ar.digest(ar.read_harness(self.candidate)),
            "evaluator": "separate-replay-run",
            "risk": risk,
            "cases": cases if cases is not None else self.cases(),
        }))

    def test_gate_and_promote_then_rollback(self):
        self.write_report()
        self.assertEqual(ar.evaluate(self.root, self.candidate, self.report)["gains"], 1)
        result = ar.promote(self.root, self.candidate, self.report, False)
        self.assertEqual(result["status"], "promoted")
        self.assertEqual(ar.target(self.root).read_text(), "Improved policy\n")
        self.assertEqual(ar.rollback(self.root, result["revision"])["status"], "rolled_back")
        self.assertEqual(ar.target(self.root).read_text(), "Original policy\n")
        self.assertEqual(len((self.root / ".active-rsi" / "events.jsonl").read_text().splitlines()), 2)

    def test_regression_rejected(self):
        cases = self.cases()
        cases[1]["candidate_pass"] = False
        self.write_report(cases)
        with self.assertRaisesRegex(ar.GateError, "regresses"):
            ar.evaluate(self.root, self.candidate, self.report)

    def test_mismatched_hash_rejected(self):
        self.write_report()
        self.candidate.write_text("Changed after evaluation")
        with self.assertRaisesRegex(ar.GateError, "mismatched"):
            ar.evaluate(self.root, self.candidate, self.report)

    def test_high_risk_requires_user_approval(self):
        self.write_report(risk="high")
        with self.assertRaisesRegex(ar.GateError, "require actual user approval"):
            ar.promote(self.root, self.candidate, self.report, False)
        self.assertEqual(ar.target(self.root).read_text(), "Original policy\n")
        self.assertEqual(ar.promote(self.root, self.candidate, self.report, True)["status"], "promoted")

    def test_training_only_results_do_not_count(self):
        cases = self.cases()
        cases[0]["split"] = "train"
        self.write_report(cases)
        with self.assertRaisesRegex(ar.GateError, "3 held-out"):
            ar.evaluate(self.root, self.candidate, self.report)

    def test_safety_failure_rejected(self):
        cases = self.cases()
        cases[2]["safety_violation"] = True
        self.write_report(cases)
        with self.assertRaisesRegex(ar.GateError, "Safety violation"):
            ar.evaluate(self.root, self.candidate, self.report)

    def test_failed_report_cannot_promote_even_with_passing_cases(self):
        self.write_report()
        report = json.loads(self.report.read_text())
        report["status"] = "failed"
        self.report.write_text(json.dumps(report))
        with self.assertRaisesRegex(ar.GateError, "not completed"):
            ar.promote(self.root, self.candidate, self.report, False)
        self.assertEqual(ar.target(self.root).read_text(), "Original policy\n")

    def test_invalid_rollback_path_rejected(self):
        with self.assertRaisesRegex(ar.GateError, "Invalid revision"):
            ar.rollback(self.root, "../../secrets")


if __name__ == "__main__":
    unittest.main()
