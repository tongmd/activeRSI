---
name: active-rsi
description: Run wake/sleep cycles for a personal agent that actively discovers user requirements and improves its task and discovery policies.
---

# activeRSI

The goal is to discover what would help the user next, turn validated needs into working behavior, and improve how the next discovery cycle runs.

## Wake

Work on the current request with the workspace's `harness/AGENT.md`. Record concise, authorized requests, corrections, and actual outcomes with `observe`. Inspect `wake` for pending hypotheses and the next suggested probe. A suggested question is not an instruction to interrupt: consider whether the answer changes the current task. Record the actual reply with `feedback`; a rejected hypothesis must not become a requirement.

## Dream

Run `dream` for a bounded offline cycle. Compare competing explanations for unmet needs, propose a low-cost discriminating probe, and produce a candidate harness. The candidate may change task execution or the discovery strategy itself. Do not contact the user or execute proposed external actions during this phase. Imagined scenarios remain hypotheses.

## Evaluate and adopt

Keep the evaluation cases separate from the proposal input. Run the baseline and candidate using `scripts/evaluate.py` with the same evaluator and per-case budget. Review actual outcomes, regressions, question burden, and cost. Promote a passing candidate with `scripts/active_rsi.py`; load the new harness in the next task and dream cycle. Keep the revision identifier for rollback.

A host integrates the runtime by loading the active harness, supplying authorized observations, and configuring a trusted model adapter and evaluator. The bundled demo uses synthetic inputs and a deterministic evaluator. Commands and adapter formats are in [README.md](README.md).
