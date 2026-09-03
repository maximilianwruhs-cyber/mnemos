# Package Status

## Completed

- Portable source folder assembled from the live MNEMOS implementation.
- Current-state specification, implementation guide, historical blueprint, and memory protocol included.
- Current deterministic scripts and all ten mandatory regression entry points included.
- Autonomy configuration, clean state templates, root context templates, six reusable skills, verifier prompt, installer, and package verifier included.
- Operator-specific memory, live queues, state, audit history, snapshots, uploads, schedules, and credentials deliberately excluded.
- The installer was exercised against a clean temporary target and installed 60 files; all essential paths were present.
- The ten bundled regression suites passed during package construction.
- A generic credential scan produced two false positives because `handoff.py` and `test_handoff.py` intentionally contain scanner patterns and synthetic fixtures. The verifier now excludes only those two scanner source files from the generic pass; their behavior remains covered by the mandatory handoff test suite.

## Runtime caveat

The SiemensGPT code sandbox stopped staging any FileStore attachment after the initial package build, even for a single small Python file, while code calls without attachments continued to work. This prevented one final rerun of the corrected package verifier and prevented rebuilding a fresh convenience ZIP in this session. The persisted source folder is the canonical deliverable; run `python verify_kit.py` after transferring it to the target device.

## Target-device acceptance

A target installation is accepted only when:

1. `python verify_kit.py` exits 0.
2. `python install.py --target <empty-workspace>` exits 0.
3. All installed root templates are customized and runtime assumptions are reprobed.
4. The installed ten-suite regression gate passes.
5. Snapshot and rollback rehearsals pass.
6. Full-scope health is GREEN.
7. Any schedule is created separately with explicit operator approval.
