# Package Status

## Completed

- Portable source folder assembled from the live MNEMOS implementation.
- Current-state specification, implementation guide, historical blueprint, and memory protocol included.
- Current deterministic scripts and all twelve mandatory regression entry points included, including `evidence.py`, `evidence_migrate.py`, and `test_evidence.py`.
- Autonomy configuration, clean state templates, root context templates, six reusable skills, verifier prompt, installer, and package verifier included.
- Operator-specific memory, live queues, state, audit history, snapshots, uploads, schedules, and credentials deliberately excluded.
- A post-snapshot install into an empty temporary target exited zero and included both the scanner module and its regression test; the earlier package build installed 60 files before these additions.
- The original ten bundled regression suites passed during package construction. The added secret-gate suite passes its focused run. The evidence-ledger suite (`test_evidence.py`) is the twelfth and passes its focused run.
- The generic credential pass excludes the handoff and memory scanner implementations plus their synthetic fixtures; the dedicated mandatory suites cover both scanners.

## Evidence-model verification (2026-09-03, Windows workstation)

The evidence-bearing note model was verified end-to-end on this Windows workstation. Target-runtime status: **Implemented; target-runtime 12-suite verification pending.**

- **Focused gate (9 commands, all exit 0):** `test_evidence` (64), `test_mnemos` (17), `test_health` (9), `test_handoff` (22), `test_autonomy_dispatcher` (7), `test_recall` (16, 2 skipped), `test_vecidx` (12 skipped), `test_secretscan` (10), and `recall.py --selftest` (9/9).
- **CLI lifecycle smoke:** `memory_note.py create` -> `append-evidence` (SUPPORT + CHALLENGE) -> `graphcheck` reported `S/C = 2/1`, state `contested`, exit 1 -> `snapshot` -> non-lossy `distill` left one L2 stub and one L3 full note with its full Evidence ledger preserved -> `mnemos.py` VERDICT PASS -> `recall` retrieved the distilled note by an evidence-quote term. No complete secret appeared in any output.
- **Migration planner:** a decision-less corpus reported `ready=false` (BLOCKED rows); the same corpus with explicit ADD/ARCHIVE decisions reported `ready=true` with exact note accounting and projected L2 lines/bytes. Reports were byte-identical across two runs and input hashes were unchanged (side-effect-free).
- **Clean install:** `install.py` into an empty external target exited 0 (87 files); `scripts/evidence.py`, `scripts/evidence_migrate.py`, and `scripts/test_evidence.py` were present; the four installed suites (`test_evidence`, `test_mnemos`, `test_health`, `test_recall`) each exited 0.
- **Package verifier:** `python verify_kit.py` — required files, 37-file Python parse, JSON parse, generic credential scan, and the 12-suite length check all PASS. `verify_kit` was corrected to scope its walk to real package files (skip gitignored scaffolding). The only remaining failures are the known environment-specific `snapshot`/`tick`/`evolution` selftests (byte-exact restore and `os.unlink` semantics on this Windows filesystem); those three modules are byte-identical to the base commit and are not part of the evidence work. `verify_kit` exit remains nonzero here and is **not** reported as green.

Remaining external gate: run the full 12-suite `regression.json` and `verify_kit.py` to final `PASS` on the SiemensGPT/POSIX target runtime, where `snapshot`/`tick`/`evolution` pass.

## Runtime caveat

The SiemensGPT code sandbox stopped staging any FileStore attachment after the initial package build, even for a single small Python file, while code calls without attachments continued to work. This prevented one final rerun of the corrected package verifier and prevented rebuilding a fresh convenience ZIP in this session. The persisted source folder is the canonical deliverable; run `python verify_kit.py` after transferring it to the target device.

## Target-device acceptance

A target installation is accepted only when:

1. `python verify_kit.py` exits 0.
2. `python install.py --target <empty-workspace>` exits 0.
3. All installed root templates are customized and runtime assumptions are reprobed.
4. The installed twelve-suite regression gate passes.
5. Snapshot and rollback rehearsals pass.
6. Full-scope health is GREEN.
7. Any schedule is created separately with explicit operator approval.
