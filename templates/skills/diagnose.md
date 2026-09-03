---
name: diagnose
description: Systematically reproduce, isolate, and fix a defect instead of guessing. Use when the user runs /diagnose, reports that something is broken or wrong, or when output from a previous step failed unexpectedly.
---

# /diagnose — Reproduce Before You Fix

Ported from the pattern in `mattpocock/skills` (`/diagnose`), adapted to this runtime.
Source: https://github.com/mattpocock/skills — reimplemented, not copied.

## Hard rule

**No fix ships before a reproduction exists.** If you cannot reproduce it, you are
not debugging, you are redecorating. Say so rather than patching hopefully.

## Procedure

1. **Capture the symptom verbatim.** Exact error text, exact input, exact expected
   vs. actual. Quote it. Do not paraphrase an error message.

2. **Reproduce in one sandbox call.** `/tmp` is wiped between calls and no state
   survives — stage every input via `file_paths` and run setup, trigger, and assertion
   end-to-end in a single `ExecutePythonCode` invocation. Print the failure.
   A reproduction that only exists in prose does not count.

3. **Shrink it.** Halve the input, remove dependencies, drop steps — until the smallest
   thing that still fails. Record what stopped mattering; that is the diagnosis boundary.

4. **Form one hypothesis at a time.** State it, predict what you would observe if true,
   test that prediction. Discard on contradiction. Never change two things per iteration.

5. **Check the known-traps list before theorising.** Most failures here are environmental,
   not logical:
   - No network egress — `requests`/`urllib`/`pip` always fail
   - `import cv2` is unavailable despite the docs
   - Writing to `/code-interpreter-output/` raises EROFS; write to `/tmp`
   - `fs_read_lines` returns empty with a success status on files over ~20 KB
   - `fs_glob` reports `sizeBytes: 0` for everything; use `fs_file_info`
   See `MEMORY.md` for the authoritative list.

6. **Fix the cause, not the symptom.** Then re-run the reproduction and show that it
   now passes. Also re-run the full original case — shrinking can hide a second bug.

7. **Persist the lesson.** If the root cause was non-obvious and durable, write an
   atomic note via `/memory` with Type `Failure-Mode` or `Gotcha`, confidence VERIFIED,
   and provenance pointing at the reproduction.

## Report format

- **Symptom** — verbatim
- **Reproduction** — minimal case, with the command that triggers it
- **Root cause** — one sentence, mechanistic
- **Fix** — what changed and why that addresses the cause
- **Verification** — the passing run, quoted

## Failure modes

- Fixing on first hypothesis without testing it.
- Declaring success without re-running the reproduction. Unverified stays labelled unverified.
- Swallowing the original error text and replacing it with a summary.
