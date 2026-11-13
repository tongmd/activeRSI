#!/usr/bin/env python3
"""Conservative harness promotion/rollback; not a model or an evaluator."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile


class GateError(ValueError):
    pass


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_harness(path: Path) -> bytes:
    if not path.is_file() or path.is_symlink():
        raise GateError(f"Harness must be a regular file: {path}")
    data = path.read_bytes()
    if not data.strip() or len(data) > 100_000:
        raise GateError("Harness must be nonempty and at most 100 KB")
    try:
        data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise GateError("Harness must be UTF-8") from exc
    return data


def target(root: Path) -> Path:
    return root / "harness" / "AGENT.md"


def evaluate(root: Path, candidate: Path, report_path: Path) -> dict:
    baseline = read_harness(target(root))
    proposed = read_harness(candidate)
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise GateError(f"Invalid evaluation report: {exc}") from exc
    if not isinstance(report, dict):
        raise GateError("Evaluation report must be an object")
    if "status" in report and report["status"] != "completed":
        raise GateError("Evaluation has not completed successfully")
    for key, data in (("baseline_sha256", baseline), ("candidate_sha256", proposed)):
        if report.get(key) != digest(data):
            raise GateError(f"Stale or mismatched {key}")
    evaluator = report.get("evaluator")
    if not isinstance(evaluator, str) or len(evaluator.strip()) < 3:
        raise GateError("Provide the independent evaluator identity")
    risk = report.get("risk")
    if risk not in ("low", "high"):
        raise GateError("Risk must be 'low' or 'high'")
    cases = report.get("cases")
    if not isinstance(cases, list):
        raise GateError("cases must be a list")
    seen = set()
    heldout = []
    for case in cases:
        if not isinstance(case, dict):
            raise GateError("Each case must be an object")
        cid = case.get("id")
        if not isinstance(cid, str) or not cid.strip() or cid in seen:
            raise GateError("Each test must have a unique nonempty id")
        seen.add(cid)
        if case.get("split") not in ("heldout", "train"):
            raise GateError("Test split must be heldout or train")
        for flag in ("baseline_pass", "candidate_pass", "safety_violation"):
            if type(case.get(flag)) is not bool:
                raise GateError(f"{cid}: {flag} must be a boolean")
        if case["safety_violation"]:
            raise GateError(f"Safety violation in test {cid}")
        if case["split"] == "heldout":
            heldout.append(case)
    if len(heldout) < 3:
        raise GateError("At least 3 held-out tests are required")
    gains = sum(not c["baseline_pass"] and c["candidate_pass"] for c in heldout)
    losses = sum(c["baseline_pass"] and not c["candidate_pass"] for c in heldout)
    if losses:
        raise GateError(f"Candidate regresses on {losses} held-out tests")
    if not gains:
        raise GateError("Candidate has no verified held-out improvement")
    return {
        "status": "eligible",
        "risk": risk,
        "baseline_sha256": digest(baseline),
        "candidate_sha256": digest(proposed),
        "heldout": len(heldout),
        "gains": gains,
        "losses": losses,
        "evaluator": evaluator.strip(),
    }


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".active-rsi-next-")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def audit(root: Path, event: dict) -> None:
    # No raw user utterances, documents, or profiles in the audit receipt.
    audit_dir = root / ".active-rsi"
    audit_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (audit_dir / "events.jsonl").open("a", encoding="utf-8") as stream:
        os.chmod(audit_dir / "events.jsonl", 0o600)
        stream.write(json.dumps(event, ensure_ascii=True, sort_keys=True) + "\n")


def _promote(root: Path, candidate: Path, report_path: Path, user_approved: bool) -> dict:
    result = evaluate(root, candidate, report_path)
    if result["risk"] == "high" and not user_approved:
        raise GateError("High-risk changes require actual user approval (--user-approved)")
    old = read_harness(target(root))
    new = read_harness(candidate)
    revisions = root / ".active-rsi" / "revisions"
    revisions.mkdir(parents=True, exist_ok=True, mode=0o700)
    rev = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + digest(old)[:12] + ".md"
    if digest(old) != result["baseline_sha256"] or digest(new) != result["candidate_sha256"]:
        raise GateError("Harness changed during evaluation")
    rev_file = revisions / rev
    with rev_file.open("xb") as stream:
        os.chmod(rev_file, 0o600)
        stream.write(old)
    atomic_write(target(root), new)
    audit(root, {"kind": "promote", "revision": rev, **result})
    return {**result, "revision": rev, "status": "promoted"}


def _rollback(root: Path, revision: str) -> dict:
    if re.fullmatch(r"\d{8}T\d{12}Z-[0-9a-f]{12}\.md", revision) is None:
        raise GateError("Invalid revision ID")
    previous = read_harness(root / ".active-rsi" / "revisions" / revision)
    old_sha = digest(read_harness(target(root)))
    atomic_write(target(root), previous)
    new_sha = digest(previous)
    result = {"status": "rolled_back", "revision": revision, "from_sha256": old_sha, "to_sha256": new_sha}
    audit(root, {"kind": "rollback", **result})
    return result


@contextmanager
def mutation_lock(root: Path):
    directory = root / ".active-rsi"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (directory / "mutation.lock").open("a") as stream:
        os.chmod(directory / "mutation.lock", 0o600)
        fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def promote(root: Path, candidate: Path, report_path: Path, user_approved: bool) -> dict:
    with mutation_lock(root):
        return _promote(root, candidate, report_path, user_approved)


def rollback(root: Path, revision: str) -> dict:
    with mutation_lock(root):
        return _rollback(root, revision)


def inspect(root: Path) -> dict:
    revisions = root / ".active-rsi" / "revisions"
    return {
        "harness": str(target(root)),
        "sha256": digest(read_harness(target(root))),
        "revisions": sorted(p.name for p in revisions.glob("*.md")) if revisions.is_dir() else [],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="activeRSI: evidence-gated harness evolution")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("inspect")
    for action in ("gate", "promote"):
        command = commands.add_parser(action)
        command.add_argument("--candidate", required=True, type=Path)
        command.add_argument("--report", required=True, type=Path)
        if action == "promote":
            command.add_argument("--user-approved", action="store_true")
    undo = commands.add_parser("rollback")
    undo.add_argument("--revision", required=True)
    args = parser.parse_args()
    try:
        if args.command == "inspect":
            result = inspect(args.root)
        elif args.command == "gate":
            result = evaluate(args.root, args.candidate, args.report)
        elif args.command == "promote":
            result = promote(args.root, args.candidate, args.report, args.user_approved)
        else:
            result = rollback(args.root, args.revision)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (OSError, GateError) as exc:
        print(json.dumps({"status": "rejected", "reason": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
