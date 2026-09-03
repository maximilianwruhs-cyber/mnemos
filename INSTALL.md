# Installation and Bring-up

## 1. Requirements

Minimum:

- Python 3.11 or newer; the source system uses Python 3.12.
- A writable persistent workspace.
- A temporary execution area.
- A mechanism to preload root context files or inject their content into the agent prompt.
- A scheduler only if unattended operation is desired.

Optional libraries are runtime-specific. Run `scripts/doctor.py` and adapt its probes rather than assuming the source machine’s library inventory.

## 2. Verify the transfer

From the package root:

```bash
python verify_kit.py
```

This validates required files, JSON, Python parsing, forbidden live-state files, credential-like content, and the ten bundled regression suites. Generate a transport-level SHA-256 checksum after downloading the folder or archive on the target device.

## 3. Install

```bash
python install.py --target /absolute/path/to/mnemos-workspace
```

The target must be empty unless `--force` is supplied. Existing files are never overwritten silently.

The installer copies:

- sanitized root templates;
- scripts and tests;
- autonomy configuration;
- reusable skills;
- verifier specification;
- documentation;
- clean state templates;

It creates empty memory categories, queue directories, audit logs, handoff storage, evolution directories, artifact storage, and snapshot storage.

## 4. Customize before first use

Edit these files in the target workspace:

1. `IDENTITY.md` — name and identity metadata.
2. `SOUL.md` — values and named failure modes.
3. `USER.md` — operator contract and preferences; keep it at or below 40 lines.
4. `AGENTS.md` — exact invocations and target-runtime facts; keep it at or below 120 lines.
5. `MEMORY.md` — runtime manifest and initial durable notes; keep it at or below 200 lines and 12,288 bytes.
6. `autonomy/config/policy.json` — review every approved scope, action, path prefix, retry, and lease limit.
7. `autonomy/config/invariants.json` — adapt runtime paths and debris patterns.
8. `autonomy/config/health-scope.json` — confirm complete scope for the target layout.

Never copy runtime-specific claims such as network availability, sandbox paths, installed libraries, or output-directory behavior without reproving them.

## 5. Initialize and verify

Run:

```bash
python scripts/autonomy_dispatcher.py --root autonomy --initialize
python scripts/doctor.py
```

Then run all suites listed in `autonomy/config/regression.json`. A suite with every test skipped is a failure.

For the first health run, build a complete manifest and store inventory for the target workspace. Health is manifest-scoped: partial staging can only prove partial health.

## 6. Rehearse safety paths

Before unattended use:

1. Create and verify a snapshot.
2. Mutate a disposable file and restore it byte-exact.
3. Rehearse rollback of a newly created file; restoration must delete it.
4. Confirm path traversal is rejected.
5. Confirm an immutable-path proposal is rejected.
6. Confirm an all-skipped regression suite fails.
7. Confirm append-only audit history cannot be rewritten.
8. Confirm repeated failures open the circuit breaker.

## 7. Enable autonomy gradually

Recommended order:

1. Identity and procedural files.
2. Memory substrate.
3. Auditor and graph integrity.
4. Health and environment checks.
5. Dispatcher in manual mode.
6. Manifest-driven persistence.
7. Bounded evolution with probation.
8. Scheduling only after explicit approval and one witnessed manual run.

There is no phase eight that widens the envelope automatically. The bounded envelope is the architecture, not a temporary inconvenience.

## 8. Scheduling

Schedules are not included because schedule IDs, time zones, and execution mechanisms are device-specific. If schedules are added:

- use the target platform’s real scheduling API;
- store cron in UTC;
- account for daylight-saving changes if local wall time matters;
- run the bounded tick before the health pass;
- treat counters as firing evidence, not success evidence;
- require persisted audit/artifact evidence before claiming completion.
