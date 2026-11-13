# activeRSI

**No memory system. The agent's memory is a user-preference-driven AutoHarness.**

activeRSI replaces RecallOS's passive preference-store model with an active, falsifiable self-improvement loop for personal agents. The durable artifact is *what the agent now knows how to do for the user* — a versioned, executable harness — rather than a profile it recalls in every prompt.

**Wake → Discover requirements → Modify harness → Independently evaluate → Promote or rollback → Dream → Improve how discovery itself works.**

The agent should not assume that repeated actions reveal hidden intent. It should form alternative hypotheses and actively select the cheapest respectful experiment that could distinguish them. A sleep cycle generates hypotheses and candidate harness changes, not imagined feedback. The user retains control over consequential actions.

## What's in this repository

- [`SKILL.md`](SKILL.md): the compact transferable skill for a host agent.
- [`harness/AGENT.md`](harness/AGENT.md): the current mutable operational harness, the only runtime adaptation artifact.
- [`references/protocol.md`](references/protocol.md): evidence/evaluation format and research criteria.
- [`scripts/active_rsi.py`](scripts/active_rsi.py): dependency-free promotion gate, harness versioning and rollback.
- [`tests/`](tests/): deterministic gate and rollback tests.

No memory database, no recall tool, no long-term user-profile summaries, no daemon. Git retains source history; `.active-rsi/` keeps local revision receipts and hashes, not raw conversations. A host model proposes harness changes; an **independent evaluator** produces the results; the helper promotes passing proposals. The helper **does not** run an LLM, execute the evaluations, infer user desires, schedule background work, or certify the report provenance.

## Try it

```bash
python3 -m unittest discover -s tests -v
python3 scripts/active_rsi.py inspect
python3 scripts/active_rsi.py gate --candidate /path/to/candidate.md --report /path/to/results.json
python3 scripts/active_rsi.py promote --candidate /path/to/candidate.md --report /path/to/results.json
python3 scripts/active_rsi.py rollback --revision REVISION_ID
```

The candidate and report are produced by your agent and external evaluation, respectively. For `risk: high`, promotion requires `--user-approved` after real authorization. Running the skill in a host requires loading `SKILL.md` and `harness/AGENT.md` into the host's own instruction pipeline. Sleep cycles require a separately configured scheduler.

## Research claim to test

Learning the current user's preferences is ordinary personalization. **Recursive self-improvement** would mean that after adaptation, the agent acquires and tests requirements for *previously unseen users* more efficiently at matched question, latency and compute budgets. A few successful self-edits do not establish that claim.

## Migration

This is a deliberate redesign of the original RecallOS concept, not a compatible release. Existing CLI commands, tool APIs, profile storage, and adapter code are intentionally removed from the active tree. Old project snapshots remain available only through normal Git history; private personal data should not be copied into this new skill.

License: MIT.
