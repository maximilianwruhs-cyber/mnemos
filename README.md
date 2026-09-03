# MNEMOS Portable Implementation Kit

**Package date:** 2026-08-30  
**Purpose:** Rebuild the MNEMOS memory, governance, verification, autonomy, and bounded-evolution substrate on another device without copying operator-private memory or live runtime state.

## Start here

1. Read `docs/MNEMOS-CURRENT-STATE.md` for the implemented architecture and evidence boundaries.
2. Read `INSTALL.md` for the exact installation sequence.
3. Read `PORTABILITY-NOTES.md` before changing runtime-dependent assumptions.
4. Run `python verify_kit.py` in this package.
5. Run `python install.py --target /path/to/new/workspace`.
6. Edit the generated root templates for the new operator and runtime.
7. Run the installed regression gate before enabling any schedule.

## Package contents

- `docs/` — current-state specification, implementation guide, memory protocol, and historical blueprint.
- `templates/root/` — sanitized initial `SOUL.md`, `IDENTITY.md`, `AGENTS.md`, `USER.md`, `MEMORY.md`, and `HEARTBEAT.md`.
- `scripts/` — current deterministic tools and regression tests.
- `autonomy/config/` — policy, schema, invariants, regression, evolution, persistence, and health-scope configuration.
- `templates/autonomy-state/` — clean initial circuit-breaker and probation state.
- `Skills/` — reusable slash-command skills.
- `verifier/` — verifier runtime specification.
- `install.py` — deterministic installer/bootstrapper.
- `verify_kit.py` — package integrity, parsing, configuration, and regression checker.
- `verify_kit.py` — dynamically inventories and validates the package on the target device. A transport-level checksum can be generated after download with the target operating system’s SHA-256 tool.

## Deliberate exclusions

This kit does **not** copy:

- Operator-specific `USER.md` content.
- Live `MEMORY.md` notes, daily logs, decisions, preferences, or archives.
- Live queue items, audit history, snapshots, leases, circuit state, probation history, or attestations.
- Uploads, generated artifacts, credentials, secrets, or schedules.
- SiemensGPT-specific schedule IDs.

Those exclusions are a feature, not missing luggage. A portable implementation should inherit architecture, not somebody else’s memory and live state.

## Authority order

1. Live code and configuration after installation.
2. `docs/MNEMOS-CURRENT-STATE.md`.
3. `docs/MNEMOS-IMPLEMENTATION-GUIDE.md`.
4. Historical blueprint.

## Security boundary

Do not enable unattended execution until:

- all tests pass on the target device;
- runtime assumptions have been reprobed;
- paths and permissions have been reviewed;
- the policy action surface is accepted;
- snapshot and rollback have been rehearsed;
- schedule creation is explicitly approved.
