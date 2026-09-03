---
name: "memory"
description: "Maintain the MNEMOS memory substrate: status, update, prune, or reflect."
metadata:
  execution-mode: "inline"
  when-to-use: "When the user runs /memory, or asks to record, review, prune, or audit persistent memory."
---

# /memory — MNEMOS substrate maintenance

Subcommand: `$ARGUMENTS` — one of `status`, `update`, `prune`, `reflect`.
If empty or unrecognised, run `status` and say that is what you did.

## Invariants

- L2 is `MEMORY.md`: auto-loaded, cap 200 lines / 12 KB, maximum 12 atomic notes.
- L3 is `Memory/`: retrieved on demand only. Never bulk-load it.
- `AGENTS.md` holds procedural rules only. Never write a memory there.
- Any write to `MEMORY.md` desynchronises `Memory/INDEX.md`. Regenerate the index in the
  SAME task. Never defer it — a stale index is a silent lie about what is remembered.
- Audit invocation: `ExecutePythonCode` with `script_file="/scripts/mnemos.py"`, staging
  `/MEMORY.md`, `/AGENTS.md`, `/Memory/INDEX.md`. Exit 0 clean, 1 findings, 2 fatal.

## status

1. Run the auditor with `--index /tmp/INDEX.md` so drift detection is active.
2. Report note count against 12, line and byte usage against caps, failures, warnings,
   and every note whose action is not KEEP.
3. Never report PASS unless the process exit code was 0. Quote the verdict line.

## update

1. Apply the three-question write gate from `AGENTS.md`: Durable? Actionable?
   Non-inferable? All three or discard. Expect to reject roughly two of every three.
2. `fs_grep` for an existing note on the same subject. If one exists, update it in place.
   Never create a near-duplicate.
3. On contradiction the newer VERIFIED note wins. Supersede the old note explicitly;
   never silently delete it.
4. Write the full schema. Every field non-empty:
   `Type` (Failure-Mode | Gotcha | Decision | Preference | Environment-Invariant | Domain-Fact),
   `Confidence` (VERIFIED | HIGH | MEDIUM | LOW), `Salience` (0.00–1.00),
   `Created`, `Last-Access`, `Freq`, `Tags`, `Links`, `Provenance`, `Observation`, `Directive`.
   `Links` must name a real note ID. `Provenance` is mandatory for VERIFIED and HIGH.
5. Salience below ~0.75 at Freq 1 scores under the prune threshold and will be flagged
   for distillation immediately. Either justify a higher salience or write it straight to L3.
6. If L2 already holds 12 notes, demote the lowest-utility note to `Memory/<category>/`
   first and leave a one-line stub behind.
7. Regenerate `Memory/INDEX.md`, then re-audit until exit code 0.

## prune

1. Run the auditor and read the `U(m)` column.
2. `U(m) < 0.35` and salience `< 0.30` — hard delete, it is operational noise.
3. `U(m) < 0.35` and salience `>= 0.30` — distil into a shorter note under `Memory/`,
   then remove the verbose original from L2.
4. Never prune a note whose directive still describes a live environment constraint,
   whatever its score says. Scores are advisory; constraints are not.
5. Regenerate the index and re-audit.

## reflect

1. Scan the session for durable learnings: failures with identified root causes,
   corrections from the operator, tool behaviour confirmed by execution.
2. Run each candidate through the write gate. Report the rejections and why —
   the discipline is the point, not the volume.
3. Persist survivors via `update`. Write a digest with `memory_daily_update`.
4. Never record a claim at VERIFIED unless a tool proved it in this session.
