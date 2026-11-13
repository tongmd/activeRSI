# activeRSI for DeepSeek Harness

Discover user requirements, dream about better ways to help, and carry evaluated improvements into the next interaction. The plugin contributes native DSH tools, a slash command, and the current operational harness to the agent's context.

The dream loop uses the model already selected in DSH. `activersi_dream` reads the workspace and returns a proposal contract. The agent reasons over that context, then calls `activersi_propose`. The runtime validates the proposal and saves a candidate. No second API key or background model process is needed.

## Install

Requires Python 3.10+, Node.js 22.19+, and DeepSeek Harness **0.2.1-rc.5**. Use the `dsh` executable from your official DSH installation.

```bash
git clone https://github.com/tongmd/activeRSI.git
cd activeRSI
python3 -m pip install -e .
cd plugins/dsh
npm install --ignore-scripts
```

Copy [`examples/activersi.patch.yml`](examples/activersi.patch.yml), replace its absolute paths, and launch your existing profile with the overlay:

```bash
dsh --profile headless --patch /absolute/path/to/activersi.patch.yml
```

The overlay loads the source entry directly. There is no published npm release yet. `coreRoot` points to the same checkout so the plugin and Python runtime use the same version. `python` can be the absolute path to a virtualenv's Python executable.

Use a private workspace outside your source checkout. One configured profile/workspace belongs to one user; all sessions of that profile share it. Give separate users separate profiles and workspace paths.

Initialize once from the DSH composer:

```text
/activersi init
/activersi observe {"kind":"correction","text":"I rewrite the daily plan before I can use it on my phone."}
/activersi status
```

Existing nonempty directories are preserved; initialization fails instead of overwriting them. You can also initialize with the core CLI before starting DSH.

## Use

Ask the DSH agent: “Run an activeRSI dream cycle from my recorded observations, save the candidate, and show me the most useful question.” The agent has these native tools:

| Tool | Effect |
| --- | --- |
| `activersi_observe` | Save an authorized request, correction, or observed outcome |
| `activersi_dream` | Read current context and obtain the proposal schema plus snapshot hash |
| `activersi_propose` | Validate and save a host-model proposal against that snapshot |
| `activersi_wake` | Select questions for the agent to present |
| `activersi_feedback` | Record the user's real answer, acceptance, or rejection |
| `activersi_status` | Read cycle status and the active harness hash |

Direct `/activersi` commands use the same operations. `/activersi dream` only prepares context; it does not schedule a model turn. `/activersi propose` accepts JSON with `snapshot` and a JSON-encoded `proposal` string. Use the normal agent conversation for the complete model-driven loop.

A workspace change between preparation and proposal submission rejects the stale proposal. Demo hypotheses and feedback are excluded from live dream context. Proposed harnesses remain inactive until evaluated and promoted through the [core evaluation workflow](../../README.md#evaluate-promote-roll-back). The next DSH prompt assembly reads the promoted harness; rollback is reflected the same way.

Sleep is explicit: the host calls the dream tools at an appropriate idle point. The plugin does not create a timer, scan unrelated conversations, send questions without the host, or automatically accept a candidate. Proposal generation alone is not a measured improvement in the agent.

Cancellation and unload terminate owned Python work and await its exit. Writes are not automatically retried after an interrupted response; inspect status first. Observation text and tool results stay within the configured user's workspace and DSH session, and are supplied to that profile's selected model when the agent uses them.

## Verify

From this directory, after installing dependencies:

```bash
npm test
```

Tests load the official Cordis, tool, command, and prompt services and invoke the actual Python runtime in temporary workspaces. They cover proposal validation, stale snapshots, feedback, dynamic harness loading, cancellation and plugin disposal. Model responses in these tests are explicit fixtures; no provider calls are made.
