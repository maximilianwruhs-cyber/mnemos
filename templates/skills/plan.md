---
name: "plan"
description: "Run a task through disciplined phases: brainstorm, written plan, test-first execution, verification."
metadata:
  execution-mode: "inline"
  when-to-use: "When the user runs /plan, or requests a non-trivial multi-step build, refactor, remediation, or analysis."
---

# /plan — disciplined execution

Objective: `$ARGUMENTS`. If empty, ask for the objective and stop.

Work the phases in order. Each gate must be met before moving on.

## Phase 1 — Brainstorm (diverge)

- Restate the objective in one sentence. If your restatement differs from the request,
  resolve that difference before doing anything else.
- List explicit constraints, implicit requirements, and the expected shape of the output.
- Propose 2–3 candidate approaches, each with a real trade-off. Not strawmen.
- Name the failure modes you expect to hit.
- **Gate:** choose one approach and state why the others lose.

## Phase 2 — Write the plan (converge)

- Create a checklist with `steps_create_plan`. Atomic steps, explicit dependencies.
- Group independent steps into **waves**. Steps in a wave have no ordering between them
  and should be issued in one parallel block; waves themselves run in sequence. If every
  step is in its own wave, the dependency analysis was lazy.
- Budget context, not just work. Any step needing bulk reading, wide search, or a long
  tool transcript goes to a sub-agent with a **fresh context** (`role="research"` to
  investigate, `"verify"` to check adversarially). Externalise its result as a file or
  memory note. State degrades as a context window fills; disk does not.
- For every step, define "done" as something observable, not something felt.
- Identify the verification tooling before writing any implementation. If no check
  exists, building one is itself a step.
- **Gate:** every step has a verifiable exit condition.

## Phase 3 — Execute, test-first

- Mark exactly one step `in_progress` before working it. Within a wave, work the steps
  in one parallel block, then close them together — never leave a finished wave open.
- Write the check before the implementation. A test that has never failed proves nothing.
- Sandbox `/tmp` is wiped between calls and staging is flat: build, test, and fix inside
  ONE `ExecutePythonCode` call, restaging inputs every time.
- Write sandbox artifacts to `/tmp/<name>`. Never target `/code-interpreter-output/`.
- Back up any file before overwriting it.
- On failure: read the actual error, form one hypothesis, fix the root cause, re-run.
  After two consecutive failures on the same step, stop and report instead of thrashing.

## Phase 4 — Verify

- Re-run every check against the **persisted** state, not the in-memory one.
- Scan for `TODO`, `FIXME`, mocks, and stubs. Zero, or the task is not done.
- Confirm generated artifacts match their sources — regenerated output should be
  byte-identical to what is persisted.
- Close the checklist honestly. A step left `in_progress` must be explained.

## Prohibitions

- NEVER weaken a test, assertion, or check to make something pass.
- NEVER report green without an exit code or tool output that demonstrates it.
- NEVER invent benchmark numbers, citations, URLs, file paths, or tool output.
- If a verification step could not run, name it explicitly as unverified. Silence is a lie.

## Report

Executive summary · files changed with exact paths · verification table with real results ·
assumptions made · what remains unverified and why.
