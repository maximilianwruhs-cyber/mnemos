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
