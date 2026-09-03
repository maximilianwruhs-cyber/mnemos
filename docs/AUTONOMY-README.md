# Autonomy Workspace

This workspace implements a deterministic, file-based autonomy protocol. All internal instructions, descriptions, schemas, status values, prompts, handoffs, and audit records use English.

## Operating invariant

One dispatcher invocation performs at most one durable state transition:

1. `reports/pending` to `tasks/pending`
2. `tasks/pending` to `handoffs/pending`
3. `handoffs/pending` to `handoffs/accepted` or `handoffs/rejected`

Queue items are JSON objects. Files move through `pending`, `processing`, and terminal directories using atomic rename operations. A renewable lease prevents overlapping owners. The circuit breaker stops processing after verification exceeds the configured retry limit.

## Authorization

Approval is checked at the boundary, not before every implementation step. An action runs without additional prompts only when both its `approval_scope` and `action.type` are listed in `config/policy.json`. Paths are independently constrained by `allowed_write_prefixes` and protected path rules.

The initial policy deliberately supports only:

- `noop`
- `write_text` below `artifacts/`

No shell execution, network access, external messaging, deletion, deployment, identity mutation, or schedule mutation is implemented.

## Running locally

```bash
python scripts/autonomy_dispatcher.py --root autonomy --initialize
python scripts/autonomy_dispatcher.py --root autonomy --owner manual-run
```

In SiemensGPT the Python sandbox cannot mutate the persistent FileStore directly. The same protocol must therefore be driven by an agent run that stages inputs, executes or validates one transition, and persists the returned files with FileStore tools. A future Agent Schedule may trigger that run, but schedule creation or modification requires separate explicit approval.

## Queue precedence

The dispatcher checks queues in this order:

1. handoffs
2. tasks
3. reports

Within a queue, lower numeric `priority` values run first; file name is the deterministic tie-breaker.

## Verification

Run:

```bash
python scripts/test_autonomy_dispatcher.py
```

The regression suite covers the complete approved action cycle, authorization rejection, path traversal rejection, lease exclusion, circuit breaking, one-transition behavior, and English audit metadata.
