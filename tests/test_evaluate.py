"""Synthetic subprocess fixtures test orchestration, not model improvement."""
import json
import os
import subprocess
import time
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import active_rsi as gate
import evaluate as runner


class EvaluationRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'harness').mkdir()
        gate.target(self.root).write_text('baseline')
        self.candidate = self.root / 'candidate.md'
        self.candidate.write_text('candidate')
        self.cases = self.root / 'cases.json'
        self.cases.write_text(json.dumps([
            {'id': str(i), 'split': 'heldout', 'input': {'prompt': 'synthetic task'}}
            for i in range(3)]))
        self.script = self.root / 'fixture.py'
        self.set_script("import json,sys\nr=json.load(sys.stdin)\nprint(json.dumps({'passed':r['harness']=='candidate','safety_violation':False,'questions':0,'cost':0.0,'request':r}))\n")

    def set_script(self, text):
        self.script.write_text(text)

    def run_evaluation(self, **kw):
        return runner.evaluate(self.root, self.candidate, self.cases,
                               [sys.executable, str(self.script)], **kw)

    def test_real_subprocesses_matched_budget_and_gate(self):
        report = self.run_evaluation(risk='low')
        self.assertEqual(report['status'], 'completed')
        self.assertEqual(len(report['runs']), 6)
        requests = [run['response']['request'] for run in report['runs']]
        for i in range(0, 6, 2):
            self.assertEqual(requests[i]['case'], requests[i + 1]['case'])
            self.assertEqual(requests[i]['budget'], requests[i + 1]['budget'])
            self.assertNotIn('variant', requests[i])
        path = self.root / 'report.json'
        path.write_text(json.dumps(report))
        self.assertEqual(gate.evaluate(self.root, self.candidate, path)['gains'], 3)
        self.assertEqual(gate.target(self.root).read_text(), 'baseline')

    def test_nonzero_exit_keeps_unpromotable_failure(self):
        self.set_script('raise SystemExit(7)')
        report = self.run_evaluation()
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(report['cases'], [])
        self.assertIn('code 7', report['error'])

    def test_timeout(self):
        self.set_script('import time;time.sleep(10)')
        report = self.run_evaluation(timeout=0.1)
        self.assertEqual(report['status'], 'failed')
        self.assertIn('timed out', report['error'])

    def test_output_limit(self):
        self.set_script("import sys;sys.stdout.write('x' * 2000000)")
        report = self.run_evaluation()
        self.assertEqual(report['status'], 'failed')
        self.assertIn('exceeds limit', report['error'])

    def test_nonfinite_or_wrong_typed_results_fail(self):
        for value in [
            '{"passed":true,"safety_violation":false,"questions":0,"cost":NaN}',
            '{"passed":"true","safety_violation":false,"questions":0,"cost":0}',
            '{"passed":true,"safety_violation":false,"questions":true,"cost":0}',
            '{"passed":true,"safety_violation":false,"questions":0,"cost":-1}',
        ]:
            with self.subTest(value=value):
                self.set_script('print(' + repr(value) + ')')
                report = self.run_evaluation()
                self.assertEqual(report['status'], 'failed')
                self.assertEqual(report['cases'], [])

    def test_budget_and_cost_regression_fail_closed(self):
        self.set_script("import json,sys\nr=json.load(sys.stdin)\nprint(json.dumps({'passed':True,'safety_violation':False,'questions':2 if r['harness']=='candidate' else 0,'cost':0}))")
        self.assertIn('exceeded budget', self.run_evaluation()['error'])
        self.assertIn('increases questions', self.run_evaluation(max_questions=3)['error'])

    def test_late_failure_clears_earlier_passes(self):
        data = json.loads(self.cases.read_text())
        data.append({'id': 'fail', 'split': 'heldout', 'input': {}})
        self.cases.write_text(json.dumps(data))
        self.set_script("import json,sys\nr=json.load(sys.stdin)\nif r['case']['id']=='fail':raise SystemExit(9)\nprint(json.dumps({'passed':r['harness']=='candidate','safety_violation':False,'questions':0,'cost':0}))")
        report = self.run_evaluation()
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(len(report['runs']), 6)
        self.assertEqual(report['cases'], [])
        path = self.root / 'failed.json'
        path.write_text(json.dumps(report))
        with self.assertRaises(gate.GateError):
            gate.evaluate(self.root, self.candidate, path)

    def test_case_validation_happens_before_execution(self):
        self.cases.write_text('[{"id":"x","split":"heldout","input":{}}]')
        with self.assertRaisesRegex(runner.EvaluationError, '3 held-out'):
            self.run_evaluation()

    def test_command_is_argv_not_shell(self):
        with self.assertRaises(runner.EvaluationError):
            runner.validate_command('echo forged; echo second')
        with self.assertRaises(runner.EvaluationError):
            runner.validate_command([])

    def test_invalid_cli_rerun_invalidates_existing_report(self):
        report = self.run_evaluation(risk='low')
        output = self.root / 'report.json'
        output.write_text(json.dumps(report))
        self.assertEqual(gate.evaluate(self.root, self.candidate, output)['gains'], 3)
        command = [sys.executable, str(Path(runner.__file__)), '--root', str(self.root),
                   '--candidate', str(self.candidate), '--cases', str(self.cases),
                   '--output', str(output), '--command', 'not-json']
        result = subprocess.run(command, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 2)
        failed = json.loads(output.read_text())
        self.assertEqual(failed['status'], 'failed')
        self.assertEqual(failed['cases'], [])
        with self.assertRaises(gate.GateError):
            gate.evaluate(self.root, self.candidate, output)
        output.write_text(json.dumps(report))
        self.cases.write_text('[]')
        command[-1] = json.dumps([sys.executable, str(self.script)])
        self.assertEqual(subprocess.run(command, capture_output=True, timeout=5).returncode, 2)
        self.assertEqual(json.loads(output.read_text())['cases'], [])

    @unittest.skipUnless(os.name == 'posix', 'POSIX process-group lifecycle')
    def test_success_stops_descendants_that_close_stdio(self):
        marker = self.root / 'descendant-finished'
        child = "import time,pathlib;time.sleep(0.7);pathlib.Path(" + repr(str(marker)) + ").write_text('alive')"
        self.set_script("import subprocess,sys,json\nr=json.load(sys.stdin)\nsubprocess.Popen([sys.executable,'-c'," + repr(child) + "],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\nprint(json.dumps({'passed':True,'safety_violation':False,'questions':0,'cost':0}))")
        response, _ = runner.run_command([sys.executable, str(self.script)], {}, 2)
        self.assertTrue(response['passed'])
        time.sleep(0.9)
        self.assertFalse(marker.exists())

    def test_harness_change_detected(self):
        self.set_script("import json,sys,pathlib\nr=json.load(sys.stdin)\npathlib.Path(" + repr(str(self.candidate)) + ").write_text('changed')\nprint(json.dumps({'passed':True,'safety_violation':False,'questions':0,'cost':0}))")
        report = self.run_evaluation()
        self.assertEqual(report['status'], 'failed')
        self.assertIn('changed during', report['error'])
        self.assertEqual(report['cases'], [])


if __name__ == '__main__':
    unittest.main()
