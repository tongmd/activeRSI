# Evaluation protocol

## Unit of improvement

An adaptation has a **requirement hypothesis**, **discovery action**, **harness change**, and **independently measured user utility**. A rule without a behavioral consequence is not an improvement.

## Wake / sleep separation

- Wake: observe actual user instructions, authorized behavior, real task outcomes, and feedback. Distinguish explicitly stated requirements from hypotheses.
- Sleep: propose competing explanations, ask what could falsify them, design low-burden questions or controlled comparisons, and write a candidate harness patch.
- Next wake: validate assumptions against real feedback. Do not convert a dream, LLM judge preference, or a synthetic user into asserted human preference.

## Preferred measurement

Record success on held-out user tasks, frequency of repeated corrections, number of questions/interruption burden, task cost, and unintended effects. For recursive improvement, evaluate learning speed on **new users** under matched budgets. Separate benefits of personalizing the current user from benefits of improving the adaptation algorithm itself.

The evaluator runs baseline and candidate with equal per-case budgets. The promotion helper checks the resulting report and binds it to exact harness hashes. Evaluator correctness remains the responsibility of the host: use task checks and actual outcomes, and keep the evaluator separate from the proposal generator.

## Result report JSON

`baseline_sha256` and `candidate_sha256` must be the lowercase SHA-256 hex hashes of the corresponding UTF-8 harness files. `evaluator` identifies a separate human or independent testing process. It is provenance metadata, not proof of independence.

```json
{
  "baseline_sha256": "<64 lowercase hex chars>",
  "candidate_sha256": "<64 lowercase hex chars>",
  "evaluator": "independent-replay-v1",
  "risk": "low",
  "cases": [
    {"id": "task-a", "split": "heldout", "baseline_pass": false, "candidate_pass": true, "safety_violation": false},
    {"id": "task-b", "split": "heldout", "baseline_pass": true, "candidate_pass": true, "safety_violation": false},
    {"id": "task-c", "split": "heldout", "baseline_pass": false, "candidate_pass": false, "safety_violation": false}
  ]
}
```

The gate requires at least 3 distinct held-out cases, at least one gain, no regressions, and no reported safety violation. Cases marked `train` are ignored for promotion. This simple default intentionally favors precision over recall; expand benchmark coverage before treating it as convincing scientific evidence. Any high-risk change (credentials, external communication, money, safety, privacy, or irreversible actions) requires actual explicit user approval and a host-specific safety review.

## Example

Observed: User repeatedly rejects draft research emails as generic.

Alternatives: (A) User wants paper-specific citations; (B) user wants shorter letters; (C) both, varying by recipient. Ask a single comparative question or offer two short drafts. Candidate harness change: require one independently checked paper connection and a short recipient-specific research proposal before sending any email. Holdout: unseen recipients and a case where no paper can be verified. Confirm the user still approves the new behavior. Promote only after independent testing, otherwise keep it as a proposal.

## Evaluator command

`scripts/evaluate.py` sends one JSON object per execution:

```json
{"harness":"full policy text", "case":{"id":"task-a", "split":"heldout", "input":{"task":"..."}}, "budget":{"timeout_seconds":60,"max_questions":1,"max_cost":1.0}}
```

The trusted evaluator returns:

```json
{"passed":true, "safety_violation":false, "questions":0, "cost":0.0}
```

Cost uses the evaluator's documented units consistently for both variants. Cases are a JSON list with unique IDs and at least three held-out entries. The runner records actual responses and elapsed times, rejects budget excess and increases in reported cost or questions, and clears promotable cases on failure. The subprocess timeout is enforced by the runner; question and cost accounting comes from the evaluator. For remote services, the evaluator must enforce spending limits before issuing calls. Evaluation tasks should use isolated environments to avoid repeating external side effects when comparing both variants.
