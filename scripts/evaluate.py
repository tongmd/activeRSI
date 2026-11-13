#!/usr/bin/env python3
"""Run a trusted evaluator against frozen baseline/candidate harnesses.

The command is an argv JSON array. It receives one JSON request on stdin and
must return one JSON object on stdout. Configure an independent evaluator;
this runner controls budgets and records outputs, not the evaluator's truth.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import threading
import time

import active_rsi as gate

MAX_OUTPUT = 1_048_576
MAX_ERROR = 65_536
MAX_INPUT = 2_097_152


class EvaluationError(ValueError):
    pass


def finite_number(value):
    return type(value) in (int, float) and math.isfinite(value)


def strict_json(text):
    def reject_constant(value):
        raise EvaluationError("Nonfinite JSON number")
    try:
        return json.loads(text, parse_constant=reject_constant)
    except (ValueError, UnicodeError) as exc:
        raise EvaluationError("Invalid evaluator JSON") from exc


def validate_command(command):
    if (not isinstance(command, list) or not command or
            any(not isinstance(arg, str) or not arg or '\0' in arg for arg in command)):
        raise EvaluationError("command must be a nonempty JSON array of argv strings")
    return command


def run_command(command, request, timeout):
    """Bound both streams in memory; kill the process group on failure/timeout."""
    payload = json.dumps(request, allow_nan=False).encode()
    if len(payload) > MAX_INPUT:
        raise EvaluationError("Evaluator input exceeds 2 MiB")
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, start_new_session=os.name == 'posix')
    overflow = threading.Event()
    buffers = [bytearray(), bytearray()]

    def read(stream, buffer, limit):
        try:
            while True:
                chunk = stream.read(4096)
                if not chunk:
                    break
                available = limit - len(buffer)
                buffer.extend(chunk[:available])
                if len(chunk) > available:
                    overflow.set()
                    break
        finally:
            stream.close()

    def write():
        try:
            process.stdin.write(payload)
            process.stdin.close()
        except (BrokenPipeError, OSError):
            pass

    threads = [threading.Thread(target=read, args=(process.stdout, buffers[0], MAX_OUTPUT), daemon=True),
               threading.Thread(target=read, args=(process.stderr, buffers[1], MAX_ERROR), daemon=True),
               threading.Thread(target=write, daemon=True)]
    for thread in threads:
        thread.start()
    started = time.monotonic()
    failure = None
    try:
        while True:
            if overflow.is_set():
                failure = 'Evaluator output exceeds limit'
                break
            if time.monotonic() - started > timeout:
                failure = 'Evaluator timed out'
                break
            if process.poll() is not None and not any(t.is_alive() for t in threads):
                break
            time.sleep(0.01)
    finally:
        # The evaluator owns this process group for one invocation only.
        # Also stop descendants that close stdio and outlive a successful parent.
        if os.name == 'posix' or failure is not None or process.poll() is None:
            try:
                if os.name == 'posix':
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
            except OSError:
                # A child can exit between poll and killpg on macOS.
                if process.poll() is None:
                    process.kill()
        process.wait()
        for thread in threads:
            thread.join(timeout=1)
    if failure:
        raise EvaluationError(failure)
    if process.returncode:
        raise EvaluationError(f'Evaluator exited with code {process.returncode}')
    response = strict_json(bytes(buffers[0]))
    if not isinstance(response, dict):
        raise EvaluationError('Evaluator response must be an object')
    for key in ('passed', 'safety_violation'):
        if type(response.get(key)) is not bool:
            raise EvaluationError(f'{key} must be a boolean')
    if type(response.get('questions')) is not int or response['questions'] < 0:
        raise EvaluationError('questions must be a nonnegative integer')
    if not finite_number(response.get('cost')) or response['cost'] < 0:
        raise EvaluationError('cost must be a finite nonnegative number')
    # Validate the entire response, including optional evaluator-specific detail.
    json.dumps(response, allow_nan=False)
    return response, time.monotonic() - started


def read_cases(path):
    if path.stat().st_size > MAX_OUTPUT:
        raise EvaluationError('Case file exceeds 1 MiB')
    cases = strict_json(path.read_bytes())
    if not isinstance(cases, list) or not cases or len(cases) > 1000:
        raise EvaluationError('cases must contain 1 to 1000 objects')
    seen = set()
    for case in cases:
        if not isinstance(case, dict):
            raise EvaluationError('Each case must be an object')
        cid = case.get('id')
        if not isinstance(cid, str) or not cid.strip() or cid in seen:
            raise EvaluationError('Each case needs a unique nonempty id')
        seen.add(cid)
        if case.get('split') not in ('train', 'heldout') or 'input' not in case:
            raise EvaluationError('Each case needs input and train/heldout split')
    if sum(c['split'] == 'heldout' for c in cases) < 3:
        raise EvaluationError('At least 3 held-out cases are required')
    return cases


def evaluate(root, candidate, cases_path, command, timeout=60, max_questions=1,
             max_cost=1.0, risk='high'):
    validate_command(command)
    if not finite_number(timeout) or timeout <= 0:
        raise EvaluationError('timeout must be positive and finite')
    if type(max_questions) is not int or max_questions < 0:
        raise EvaluationError('max_questions must be a nonnegative integer')
    if not finite_number(max_cost) or max_cost < 0:
        raise EvaluationError('max_cost must be nonnegative and finite')
    if risk not in ('low', 'high'):
        raise EvaluationError('risk must be low or high')
    cases = read_cases(cases_path)
    baseline = gate.read_harness(gate.target(root))
    proposed = gate.read_harness(candidate)
    budget = {'timeout_seconds': timeout, 'max_questions': max_questions, 'max_cost': max_cost}
    report = {'schema': 'activeRSI.evaluation.v1', 'status': 'running', 'risk': risk,
              'baseline_sha256': gate.digest(baseline), 'candidate_sha256': gate.digest(proposed),
              'cases_sha256': gate.digest(json.dumps(cases, sort_keys=True, allow_nan=False).encode()),
              'cases_hash_format': 'sorted-key-json',
              'evaluator': 'command:' + Path(command[0]).name,
              'command_sha256': hashlib.sha256(json.dumps(command).encode()).hexdigest(),
              'budget': budget, 'cases': [], 'runs': []}
    recorded_bytes = 0
    try:
        for case in cases:
            pair = {}
            for name, harness in (('baseline', baseline), ('candidate', proposed)):
                # No variant label is sent: both see the same task and budget.
                request = {'harness': harness.decode('utf-8'), 'case': case, 'budget': budget}
                response, seconds = run_command(command, request, timeout)
                recorded_bytes += len(json.dumps(response, allow_nan=False).encode())
                if recorded_bytes > 16 * MAX_OUTPUT:
                    raise EvaluationError('Total evaluation responses exceed 16 MiB')
                pair[name] = response
                report['runs'].append({'case_id': case['id'], 'variant': name,
                                       'elapsed_seconds': seconds, 'response': response})
                if response['questions'] > max_questions or response['cost'] > max_cost:
                    raise EvaluationError(f"{case['id']}: {name} exceeded budget")
            before, after = pair['baseline'], pair['candidate']
            if after['questions'] > before['questions'] or after['cost'] > before['cost']:
                raise EvaluationError(f"{case['id']}: candidate increases questions or cost")
            report['cases'].append({'id': case['id'], 'split': case['split'],
                                    'baseline_pass': before['passed'], 'candidate_pass': after['passed'],
                                    'safety_violation': before['safety_violation'] or after['safety_violation']})
        if gate.read_harness(gate.target(root)) != baseline or gate.read_harness(candidate) != proposed:
            raise EvaluationError('Harness changed during evaluation')
        report['status'] = 'completed'
    except (EvaluationError, OSError, ValueError, gate.GateError) as exc:
        # A partial report can never pass the existing promotion gate.
        report['status'] = 'failed'
        report['error'] = str(exc)
        report['cases'] = []
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--cases', type=Path, required=True)
    parser.add_argument('--command', required=True, help='JSON array of trusted executable and arguments')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--timeout', type=float, default=60)
    parser.add_argument('--max-questions', type=int, default=1)
    parser.add_argument('--max-cost', type=float, default=1.0,
                        help='Per-case cap in evaluator-defined cost units (same units for both variants)')
    parser.add_argument('--risk', choices=('low', 'high'), default='high')
    args = parser.parse_args()
    output_allowed = False
    try:
        protected = {gate.target(args.root).resolve(), args.candidate.resolve(), args.cases.resolve()}
        if args.output.resolve() in protected:
            raise EvaluationError('Report output cannot overwrite evaluation inputs')
        output_allowed = True
        report = evaluate(args.root, args.candidate, args.cases, strict_json(args.command),
                          args.timeout, args.max_questions, args.max_cost, args.risk)
        gate.atomic_write(args.output, (json.dumps(report, indent=2, allow_nan=False) + '\n').encode())
        print(json.dumps({'status': report['status'], 'output': str(args.output),
                          'completed_runs': len(report['runs'])}))
        return 0 if report['status'] == 'completed' else 2
    except (OSError, ValueError, gate.GateError) as exc:
        failure = {'schema': 'activeRSI.evaluation.v1', 'status': 'failed',
                   'error': str(exc), 'cases': [], 'runs': []}
        if output_allowed:
            try:
                # Invalidate an earlier successful report even on preflight failure.
                gate.atomic_write(args.output, (json.dumps(failure, indent=2) + '\n').encode())
            except OSError as write_error:
                failure['report_write_error'] = str(write_error)
        print(json.dumps(failure))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
