# MNEMOS Evidence-Accumulating DISTIL Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every active full MNEMOS note evidence-bearing, preserve that evidence through explicit L2-to-L3 DISTIL, and expose derived support/challenge state without changing retrieval or forgetting scores.

**Architecture:** A new pure `scripts/evidence.py` module owns evidence parsing, validation, canonical rendering, secret checks, duplicate identity, and derived counts. Existing `mnemos.py` and `graphcheck.py` consume that interface for L2/L3 audits and indexes; existing `memory_note.py` remains the single cross-tier mutation seam and gains evidence-safe create, append, and DISTIL operations. A dry-run migration planner accounts for every active note but never invents evidence or applies corpus changes.

**Tech Stack:** Python 3.11+ standard library, Markdown with one-line embedded JSON records, `unittest`, existing MNEMOS flat `/tmp` staging convention.

**Spec:** `docs/specs/2026-09-03-mnemos-evidence-accumulating-distil-design.md`

## Global Constraints

- Evidence is mandatory on every active full note; every such note has at least one `SUPPORT` record.
- An L2 non-stub `### [MEM-YYYY-NNNN]` note and a non-archive L3 Markdown file declaring its own `MEM-YYYY-NNNN` are full notes.
- L2/L3 stubs, non-note daily documents, and `Memory/_archive/**` are exempt.
- Each Evidence object has exactly the string keys `date`, `stance`, `source`, `quote`; stance is `SUPPORT` or `CHALLENGE`.
- `date` is ISO `YYYY-MM-DD`, never future; `source` is 1–240 characters; `quote` is 1–280 characters; neither contains a physical newline.
- Maximum 16 Evidence records per note. Exact canonical duplicates fail. Semantic/source independence remains operator judgment.
- `S/C` counts are derived, never stored. Do not add `Proofs`, `Evidence-Count`, or another counter field.
- Evidence append never changes `Freq`, `Last-Access`, `Confidence`, `Salience`, `Observation`, `Directive`, utility, or retrieval weights.
- A `CHALLENGE` is structurally valid but contested and produces WARN until operator resolution.
- Every Evidence `source` and `quote` passes `secretscan.scan`; findings expose masked previews only.
- DISTIL remains advisory and operator-triggered. Snapshot precedes mutation. No heuristic automatically deletes, distils, rewrites, or promotes confidence.
- Markdown is the sole durable authority. Do not create per-note evidence sidecars, a database, daemon, network call, or LLM extractor.
- Clean cutover: no optional legacy schema, compatibility flag, alias, or deprecated parser remains after migration.
- Preserve the 200-line / 12,288-byte L2 caps. Demote notes rather than raising either cap.
- Flat sandbox invocations stage `evidence.py` and `secretscan.py` beside every importing script; `memory_note.py distill` also stages `snapshot.py`.
- The current Windows workstation has pre-existing `verify_kit.py` failures in snapshot/tick/evolution and an intermittent health file-replace case. Do not modify those unrelated modules in this plan; the full mandatory gate remains a target-runtime deployment check.

---

## File Structure and Responsibilities

**Create**

- `scripts/evidence.py` — pure Evidence parser, validator, canonical renderer, appender, and derived summary.
- `scripts/test_evidence.py` — one mandatory vertical regression suite covering schema, mutation, graph/health propagation, and migration planning.
- `scripts/evidence_migrate.py` — deterministic dry-run corpus inventory/planner; reads operator decisions, emits a report, never writes the corpus.

**Modify**

- `scripts/mnemos.py` — require Evidence in L2 full notes, map findings, retain reports, render `S/C` in `INDEX.md`.
- `scripts/test_mnemos.py` — migrate the canonical fixture and cover missing/contested evidence plus index output.
- `scripts/test_secretscan.py` — migrate its otherwise-valid full-note fixture to the mandatory schema.
- `scripts/memory_note.py` — evidence-valid new-note creation, atomic Evidence append, explicit DISTIL transition, subcommand CLI.
- `scripts/test_health.py` — migrate `memory_note.create` coverage and test cross-tier WARN classification.
- `scripts/graphcheck.py` — validate active L3 full notes, report FAIL/WARN, render derived `S/C` and state in `INDEX-L3.md`.
- `scripts/health.py` — propagate graph Evidence warnings as AMBER instead of reporting cross-tier GREEN.
- `scripts/test_recall.py` — prove evidence-bearing notes remain retrievable without a count-based score term.
- `scripts/doctor.py` — stage/run the evidence regression and name transitive dependencies.
- `autonomy/config/health-scope.json` — require and fingerprint `evidence.py`.
- `autonomy/config/invariants.json` — protect `evidence.py` as immutable enforcement code.
- `autonomy/config/regression.json` — register `test_evidence.py` as mandatory suite 12.
- `verify_kit.py` — expect and report 12 suites; exclude a scanner fixture only if its runtime-constructed strings trigger the generic pass.
- `templates/root/MEMORY.md` — document the mandatory Evidence line in the active note area.
- `templates/root/AGENTS.md` — name `evidence.py` and `secretscan.py` staging requirements.
- `templates/skills/memory.md` — require Evidence on update, define append/challenge/DISTIL behavior, stage both dependencies.
- `templates/memory/INDEX.md` and `templates/memory/INDEX-L3.md` — initialize the generated `S/C` columns.
- `docs/MEMORY-PROTOCOL.md` — canonical twelfth-field and non-lossy DISTIL protocol.
- `docs/MNEMOS-CURRENT-STATE.md` — post-implementation state, module inventory, suite count, evidence boundaries.
- `docs/MNEMOS-IMPLEMENTATION-GUIDE.md` — dated amendment; preserve historical eleven-field discussion as historical.
- `INSTALL.md` and `PACKAGE-STATUS.md` — twelve-suite and staging/package verification language.
- `docs/specs/2026-09-03-mnemos-evidence-accumulating-distil-design.md` — mark implemented only after Task 8 evidence exists.

No production change is required in `scripts/recall.py`, `scripts/vecidx.py`, `scripts/secretscan.py`, `scripts/snapshot.py`, `scripts/tick.py`, `scripts/evolution.py`, or `scripts/probation.py`.

---

### Task 1: Pure Evidence Ledger Module

**Files:**
- Create: `scripts/evidence.py`
- Create: `scripts/test_evidence.py`

**Interfaces:**
- Consumes: `secretscan.scan(text: str) -> list[SecretMatch]`.
- Produces:
  - `EvidenceItem(date: datetime.date, stance: str, source: str, quote: str, canonical: str, fingerprint: bytes)`
  - `EvidenceFinding(level: str, detail: str)`
  - `EvidenceReport(items: tuple[EvidenceItem, ...], support: int, challenge: int, contested: bool, findings: tuple[EvidenceFinding, ...])`
  - `inspect(text: str, today: datetime.date) -> EvidenceReport`
  - `append(text: str, item: dict[str, str], today: datetime.date) -> str`

- [ ] **Step 1: Write failing schema and append tests**

Create `scripts/test_evidence.py`. Its loader must convert a missing module into an assertion failure, not an import error:

```python
import importlib.util
import inspect
import sys
import unittest
import tempfile
from datetime import date
from pathlib import Path

def load_module(filename):
    path = Path("/tmp") / filename
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module

evidence = load_module("evidence.py")
TODAY = date(2026, 9, 3)
NOTE_WITH_SUPPORT = ('- **Evidence:** {"date":"2026-09-02","stance":"SUPPORT",'
                     '"source":"first probe","quote":"PASS"}\n')
CHALLENGE_LINE = ('- **Evidence:** {"date":"2026-09-03","stance":"CHALLENGE",'
                  '"source":"counter-probe","quote":"FAIL"}\n')
SECOND_SUPPORT = {"date": "2026-09-03", "stance": "SUPPORT",
                  "source": "second probe", "quote": "PASS"}

class EvidenceSchemaTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(evidence, "evidence.py is not implemented")

    def test_one_support_record_is_valid_and_counted(self):
        report = evidence.inspect(NOTE_WITH_SUPPORT, TODAY)
        self.assertEqual(report.findings, ())
        self.assertEqual((report.support, report.challenge), (1, 0))
        self.assertFalse(report.contested)

    def test_missing_support_is_a_structural_failure(self):
        report = evidence.inspect("- **Directive:** Act.\n", TODAY)
        self.assertTrue(any(f.level == "FAIL" and "Evidence" in f.detail
                            for f in report.findings))

    def test_challenge_is_valid_but_contested(self):
        report = evidence.inspect(NOTE_WITH_SUPPORT + CHALLENGE_LINE, TODAY)
        self.assertEqual([f.level for f in report.findings], ["WARN"])
        self.assertEqual((report.support, report.challenge), (1, 1))
        self.assertTrue(report.contested)

    def test_append_preserves_existing_bytes_and_uses_canonical_json(self):
        updated = evidence.append(NOTE_WITH_SUPPORT, SECOND_SUPPORT, TODAY)
        self.assertTrue(updated.startswith(NOTE_WITH_SUPPORT))
        self.assertIn(
            '- **Evidence:** {"date":"2026-09-03","stance":"SUPPORT",'
            '"source":"second probe","quote":"PASS"}\n', updated)

    def test_invalid_records_fail(self):
        cases = {
            "malformed": "{bad json}",
            "unknown key": '{"date":"2026-09-03","stance":"SUPPORT","source":"x","quote":"y","extra":"z"}',
            "future": '{"date":"2026-09-04","stance":"SUPPORT","source":"x","quote":"y"}',
            "bad stance": '{"date":"2026-09-03","stance":"MAYBE","source":"x","quote":"y"}',
            "empty source": '{"date":"2026-09-03","stance":"SUPPORT","source":"","quote":"y"}',
        }
        for label, payload in cases.items():
            with self.subTest(label=label):
                report = evidence.inspect(f"- **Evidence:** {payload}\n", TODAY)
                self.assertTrue(any(f.level == "FAIL" for f in report.findings))
```

Add separate tests for source length 241, quote length 281, physical-newline rejection through a directly supplied append item, exact duplicate rejection, the 17th record, noncanonical key order/spacing, an indented or non-JSON `Evidence:` line beside a valid SUPPORT record, and a runtime-constructed fake token that must produce a masked failure without exposing the token. Add a `python -I -c`/`runpy.run_path` test that copies `evidence.py` and `secretscan.py` to an isolated stage and loads `evidence.py` from an unrelated working directory.

- [ ] **Step 2: Run the new suite and verify RED**

Run:

```bash
rm -f /tmp/evidence.py
cp scripts/secretscan.py /tmp/secretscan.py
python scripts/test_evidence.py
```

Expected: assertion failure `evidence.py is not implemented`; no syntax/import harness error.

- [ ] **Step 3: Implement the pure module**

Create `scripts/evidence.py` with an anchored sibling import and these constants:

```python
sys.path.insert(0, str(Path(__file__).resolve().parent))
import secretscan  # noqa: E402

EVIDENCE_RE = re.compile(r"^[ \t]*-[ \t]*\*\*Evidence:\*\*[ \t]*(.*)$", re.M)
KEYS = ("date", "stance", "source", "quote")
STANCES = {"SUPPORT", "CHALLENGE"}
MAX_ITEMS = 16
MAX_SOURCE = 240
MAX_QUOTE = 280
```

Use frozen dataclasses with the exact fields in the Interfaces block. Canonical rendering is:

```python
def _canonical(values: dict[str, str]) -> str:
    ordered = {key: values[key] for key in KEYS}
    return json.dumps(ordered, ensure_ascii=False, separators=(",", ":"))
```

`inspect` must scan every labeled Evidence line—including indented, empty, and non-JSON payloads—then catch `json.JSONDecodeError`, reject non-dicts, reject any key set other than `set(KEYS)`, require string values, trim only leading/trailing whitespace before validation/rendering, parse dates with `datetime.strptime(..., "%Y-%m-%d").date()`, enforce all bounds, run `secretscan.scan` independently on `source` and `quote`, and compare each source line to `- **Evidence:** {_canonical(values)}`. Build duplicate identity from `sha256(canonical.encode("utf-8")).digest()`. Count only unique valid items for `support`/`challenge`, but enforce `MAX_ITEMS` against all labeled records. After per-line validation, fail zero records, fail zero valid SUPPORT records, fail more than 16 records, and emit one WARN when at least one valid CHALLENGE exists.

Secret findings must use the same masked detail as `mnemos.scan_secrets`: `possible {kind} ({preview}) - remove before commit`. This lets the L2 integration deduplicate the shared Evidence scan against the whole-file scan without comparing raw values.

`append` must preserve every input byte and append only:

```python
separator = "" if text.endswith("\n") else "\n"
updated = text + separator + f"- **Evidence:** {_canonical(candidate)}\n"
report = inspect(updated, today)
if any(f.level == "FAIL" for f in report.findings):
    raise ValueError("; ".join(f.detail for f in report.findings if f.level == "FAIL"))
return updated
```

Validate physical newlines in the candidate before JSON rendering so escaped JSON cannot hide a multiline input.

- [ ] **Step 4: Stage both modules and verify GREEN**

Run:

```bash
cp scripts/secretscan.py scripts/evidence.py /tmp/
python scripts/test_evidence.py
```

Expected: all Evidence schema/append tests pass; the fake token never appears in output or findings.

- [ ] **Step 5: Commit Task 1**

```bash
git add scripts/evidence.py scripts/test_evidence.py
git commit -m "feat(memory): add evidence ledger module"
```

---

### Task 2: Mandatory L2 Evidence Audit and Index

**Files:**
- Modify: `scripts/mnemos.py:9-20,119-189,224-252`
- Modify: `scripts/test_mnemos.py:19-91`
- Modify: `scripts/test_secretscan.py:20-33`
- Modify: `scripts/test_evidence.py`

**Interfaces:**
- Consumes: Task 1 `evidence.inspect(text, today) -> EvidenceReport`.
- Produces: every non-stub parsed note has internal `note["evidence"]: EvidenceReport` and `note["body_start"]: int`; generated `INDEX.md` has an `S/C` column.

- [ ] **Step 1: Migrate valid fixtures and add failing L2 tests**

Add this canonical line to `VALID_NOTE` in both `test_mnemos.py` and `test_secretscan.py`:

```markdown
- **Evidence:** {"date":"2026-08-24","stance":"SUPPORT","source":"local regression","quote":"The behavior was observed."}
```

Update `test_secretscan.py::test_runpy_resolves_scanner_staged_next_to_mnemos` to copy `evidence.py` beside `mnemos.py` and `secretscan.py`; this test must fail until the new transitive dependency is staged.

Add tests to `test_mnemos.py`:

```python
def test_missing_evidence_fails(self):
    self.assert_failure(VALID_NOTE.replace(EVIDENCE_LINE, ""),
                        "at least one Evidence record")

def test_challenge_warns_without_changing_action(self):
    text = VALID_NOTE + CHALLENGE_LINE
    findings, rows, generated, _ = self.run_case(text)
    self.assertTrue(any(f.level == "WARN" and "contested" in f.detail
                        for f in findings))
    self.assertEqual(rows[0]["action"], "KEEP")
    self.assertIn("| 1/1 |", generated)

def test_evidence_counts_are_derived_in_index(self):
    _, _, generated, _ = self.run_case(VALID_NOTE)
    self.assertIn("| S/C |", generated)
    self.assertIn("| 1/0 |", generated)
    self.assertNotIn("Proofs", generated)

def test_secret_in_evidence_is_reported_once_and_masked(self):
    token = "ghp_" + "x" * 36
    text = VALID_NOTE.replace("local regression", token)
    findings, _, _, _ = self.run_case(text)
    secret_findings = [f for f in findings if f.detail.startswith("possible ")]
    self.assertEqual(len(secret_findings), 1, secret_findings)
    self.assertNotIn(token, secret_findings[0].detail)
```

- [ ] **Step 2: Run L2 tests and verify RED**

Run:

```bash
cp scripts/mnemos.py scripts/secretscan.py scripts/evidence.py /tmp/
python scripts/test_mnemos.py
```

Expected: missing Evidence is not rejected and `INDEX.md` has no `S/C` column.

- [ ] **Step 3: Integrate Evidence into `mnemos.py`**

Anchor and import `evidence` beside `secretscan`:

```python
sys.path.insert(0, str(Path(__file__).resolve().parent))
import evidence  # noqa: E402
import secretscan  # noqa: E402
```
Retain the parser's existing `body_start` local in each note dictionary so mutation/migration callers can splice the body without reparsing headings.

In `check_notes`, keep stubs exempt. For each full note, call `report = evidence.inspect(note["body"], today)`, assign `note["evidence"] = report`, and map each finding without reparsing:

```python
for item in report.findings:
    findings.append(Finding(item.level, nid, item.detail))
```

Do not add `Evidence` to the old scalar `REQUIRED_FIELDS` set: repeated records are owned by `evidence.inspect`, and duplicate failure messages add no value. Change the existing PASS detail to `schema and evidence complete` only when scalar fields and Evidence have no FAIL.

After `audit` adds both Evidence and whole-file secret findings, preserve order while deduplicating identical frozen `Finding` values:

```python
findings = list(dict.fromkeys(findings))
```

Extend the generated registry header and each row:

```python
"| ID | Title | Type | Conf. | I(m) | S | U | Action | S/C | Links |"
summary = note.get("evidence")
evidence_count = f"{summary.support}/{summary.challenge}" if summary else "-"
```

Insert `evidence_count` after `Action`. Do not touch `score_notes`, `recency`, utility constants, or row sorting.

- [ ] **Step 4: Verify L2, secret, and pure Evidence suites**

Run:

```bash
cp scripts/mnemos.py scripts/secretscan.py scripts/evidence.py /tmp/
python scripts/test_evidence.py
python scripts/test_mnemos.py
python scripts/test_secretscan.py
```

Expected: all three commands exit 0. The challenge case has action `KEEP`, proving Evidence does not affect scoring.

- [ ] **Step 5: Commit Task 2**

```bash
git add scripts/mnemos.py scripts/test_mnemos.py scripts/test_secretscan.py scripts/test_evidence.py
git commit -m "feat(memory): require evidence in L2 notes"
```

---

### Task 3: Evidence-Safe Note Creation and Append

**Files:**
- Modify: `scripts/memory_note.py:1-35`
- Modify: `scripts/test_health.py:1-36`
- Modify: `scripts/test_evidence.py`

**Interfaces:**
- Consumes: `evidence.inspect`, `evidence.append`, `mnemos.parse_notes`, `mnemos.FIELD_RE`, `snapshot.resolve_inside`.
- Produces:
  - `create(memory: Path, root: Path, nid: str, title: str, category: str, body: str, today: date) -> str`
  - `append_evidence(root: Path, relative_path: str, nid: str, item: dict[str, str], today: date) -> None`
  - CLI subcommands `create` and `append-evidence`.

- [ ] **Step 1: Add failing creation/append behavior tests**

In `test_evidence.py`, load `/tmp/memory_note.py` with `memory_note = load_module("memory_note.py")`; begin the mutation test class with `self.assertIsNotNone(memory_note, "memory_note.py is not staged")`. Add tests using real temporary files:

```python
def test_create_rejects_missing_evidence_without_writes(self):
    self.assertIn("today", inspect.signature(memory_note.create).parameters)
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        memory = root / "MEMORY.md"
        memory.write_text(EMPTY_MEMORY, encoding="utf-8")
        before = memory.read_bytes()
        with self.assertRaisesRegex(ValueError, "Evidence"):
            memory_note.create(memory, root, NID, "Title", "lessons",
                               BODY_WITHOUT_EVIDENCE, TODAY)
        self.assertEqual(memory.read_bytes(), before)
        self.assertEqual(list((root / "Memory").rglob("*.md")) if
                         (root / "Memory").exists() else [], [])

def test_create_writes_valid_full_note_and_complete_stub(self):
    rel = memory_note.create(memory, root, NID, "Title", "lessons",
                             VALID_BODY, TODAY)
    full = (root / rel).read_text(encoding="utf-8")
    stub = memory.read_text(encoding="utf-8")
    self.assertEqual(evidence.inspect(full, TODAY).support, 1)
    self.assertIn("VERIFIED", stub)
    self.assertIn("Use the verified path.", stub)
    self.assertIn(rel, stub)

def test_append_evidence_to_l3_is_atomic_and_preserves_fields(self):
    self.assertTrue(hasattr(memory_note, "append_evidence"),
                    "append_evidence is not implemented")
    relative = note_path.relative_to(root).as_posix()
    before = note_path.read_bytes()
    memory_note.append_evidence(root, relative, NID, SECOND_SUPPORT, TODAY)
    after = note_path.read_bytes()
    self.assertTrue(after.startswith(before))
    before_fields = dict(mnemos.FIELD_RE.findall(before.decode("utf-8")))
    after_fields = dict(mnemos.FIELD_RE.findall(after.decode("utf-8")))
    for name in ("Freq", "Last-Access", "Confidence", "Salience",
                 "Observation", "Directive"):
        self.assertEqual(after_fields[name], before_fields[name])
    self.assertEqual(evidence.inspect(after.decode(), TODAY).support, 2)

def test_append_evidence_splices_only_the_named_l2_note(self):
    before = memory.read_text(encoding="utf-8")
    memory_note.append_evidence(root, "MEMORY.md", NID,
                                SECOND_SUPPORT, TODAY)
    after = memory.read_text(encoding="utf-8")
    notes = {note["id"]: note for note in mnemos.parse_notes(after)}
    old_notes = {note["id"]: note for note in mnemos.parse_notes(before)}
    self.assertEqual(evidence.inspect(notes[NID]["body"], TODAY).support, 2)
    self.assertEqual(notes[OTHER_ID]["body"], old_notes[OTHER_ID]["body"])
```

Add duplicate/secret candidate cases and reject path escape, absolute paths, `Memory/_archive/**`, stubs, missing IDs, an L3 heading whose ID differs from `nid`, and any create/L2 append whose projected MEMORY exceeds 200 lines or 12,288 UTF-8 bytes; every refusal leaves all bytes identical.

Patch `os.replace` to raise before append replacement and assert the original file plus sibling directory contents are byte-identical. For `create`, let the L3 replace execute, raise on the MEMORY replace, and assert the newly created L3 file is removed and MEMORY is restored exactly; use the real-first/forced-second helper shown in Task 4.

Add an isolated `python -I -c`/`runpy.run_path` test that copies `memory_note.py`, `mnemos.py`, `evidence.py`, `secretscan.py`, and `snapshot.py` into one temporary stage and loads `memory_note.py` from an unrelated working directory.

Update `test_health.py::test_note_creation_is_bidirectional` to pass `TODAY` and a complete field block containing one SUPPORT record.

- [ ] **Step 2: Run mutation tests and verify RED**

Run:

```bash
cp scripts/memory_note.py scripts/mnemos.py scripts/evidence.py scripts/secretscan.py scripts/snapshot.py /tmp/
python scripts/test_evidence.py
python scripts/test_health.py
```

Expected: assertion failures identify the missing deterministic `today` interface and `append_evidence`; after those exist, the missing-Evidence behavior remains RED until validation is wired.

- [ ] **Step 3: Replace the shallow script with a maintainable mutation module**

Retain the existing category set and ID/path validation. Add anchored sibling imports. Require `memory.resolve() == (root / "MEMORY.md").resolve()` for `create`. Use `snapshot.resolve_inside` for every operator-supplied relative path.

Add `_check_memory_caps(text)` against `mnemos.CAPS["MEMORY.md"]` before any projected MEMORY write. Add `_atomic_replace(path, text)` that writes a same-directory UTF-8 temporary file, flushes/closes it, then calls `os.replace`; on any exception it deletes the temporary file and leaves the target unchanged.

`create` must:

1. parse current L2 notes, append a synthetic candidate heading/body in memory, and run `mnemos.check_notes` so the candidate uses the existing scalar schema and link rules;
2. reject every candidate-scoped FAIL, while ignoring the synthetic corpus's L2 occupancy finding because the candidate will live in L3;
3. extract validated `Confidence` and `Directive` fields for the stub;
4. build the L3 heading and deterministic slug/path;
5. create a stub containing ID/title, confidence, Directive, and L3 path;
6. run `_check_memory_caps` on the projected stub-bearing MEMORY text;
7. prepare both temporary files before replacing either;
8. replace the new L3 target first and MEMORY second;
9. remove the new target and restore original MEMORY bytes if the second replace fails.

`append_evidence` must resolve `relative_path` through `snapshot.resolve_inside(root, relative_path)` and refuse archive paths. For `MEMORY.md`, parse the named non-stub note, call `evidence.append` on only its body, splice the result through `body_start`/`scan_end`, and run `_check_memory_caps` before one atomic MEMORY replacement. For L3, require the first heading to declare exactly `nid`, then call `evidence.append` on the full file before one atomic replacement. A validation, identity, containment, or cap failure performs no write.

Replace the old single-mode CLI with required subcommands:

```text
memory_note.py create --memory PATH --root PATH --id MEM-ID --title TEXT --category NAME --body-file PATH --today YYYY-MM-DD
memory_note.py append-evidence --root PATH --path RELATIVE-PATH --id MEM-ID --evidence-json JSON --today YYYY-MM-DD
```

Parse `--evidence-json` with `json.loads`; pass the resulting dict to `append_evidence`. No legacy CLI alias remains.

- [ ] **Step 4: Verify creation and append GREEN**

Run the two commands from Step 2 again. Then run a direct CLI smoke in a temporary workspace for each subcommand. Expected: valid create/append exit 0; invalid Evidence exits nonzero; failed operations leave inputs byte-identical.

- [ ] **Step 5: Commit Task 3**

```bash
git add scripts/memory_note.py scripts/test_health.py scripts/test_evidence.py
git commit -m "feat(memory): enforce evidence on note mutations"
```

---

### Task 4: Explicit Non-Lossy DISTIL Transition

**Files:**
- Modify: `scripts/memory_note.py`
- Modify: `scripts/test_evidence.py`

**Interfaces:**
- Consumes: `mnemos.parse_notes`, `mnemos.check_notes`, `mnemos.score_notes`, `snapshot.load_manifest`, `snapshot.verify`, Task 3 pair-write helper.
- Produces: `distill(memory: Path, root: Path, snapshot_store: Path, snapshot_id: str, nid: str, category: str, observation: str, today: date) -> str`; CLI subcommand `distill`.

- [ ] **Step 1: Add failing DISTIL transition tests**

Create a complete L2 fixture whose `Freq=0`, `Salience=0.30`, old `Created`, and current `Last-Access` produce `action == "DISTIL"`. Include two Evidence records and a second valid L2 note for `[[MEM-2026-0002]]`, so the preserved-link assertion exercises a resolved link rather than an invalid fixture. Test:

Load `snapshot = load_module("snapshot.py")` and assert it is staged before constructing the DISTIL fixture.

```python
def test_distill_preserves_identity_directive_links_and_evidence(self):
    self.assertTrue(hasattr(memory_note, "distill"), "distill is not implemented")
    expected_rel = "Memory/lessons/MEM-2026-0001-title.md"
    snapshot_store = root / "autonomy/snapshots"
    manifest = snapshot.create(snapshot_store, root,
                               ["MEMORY.md", expected_rel], "before-distill")
    rel = memory_note.distill(memory, root, snapshot_store, manifest["id"],
                              NID, "lessons", "Compressed semantic claim.", TODAY)
    l2 = memory.read_text(encoding="utf-8")
    l3 = (root / rel).read_text(encoding="utf-8")
    self.assertIn(f"### [{NID}]", l2)
    self.assertIn("**Stub.**", l2)
    self.assertIn("VERIFIED", l2)
    self.assertIn("Use the verified path.", l2)
    self.assertIn(f"# {NID} —", l3)
    self.assertIn("Compressed semantic claim.", l3)
    self.assertEqual(evidence.inspect(l3, TODAY).support, 2)
    self.assertIn("[[MEM-2026-0002]]", l3)
```

Add tests that refuse KEEP, DELETE, and STUB notes; refuse a missing target note; refuse an existing L3 path; reject an empty/newline observation; reject an absent, corrupt, wrong-base, incomplete, or stale snapshot; and leave MEMORY plus existing files byte-identical on every refusal.

Patch only the otherwise-unreachable second-replace failure, but let the first replace execute for real:

```python
real_replace = memory_note.os.replace
calls = 0
def fail_second(source, target):
    nonlocal calls
    calls += 1
    if calls == 2:
        raise OSError("forced second replace failure")
    return real_replace(source, target)

with patch.object(memory_note.os, "replace", side_effect=fail_second):
    with self.assertRaises(OSError):
        memory_note.distill(memory, root, snapshot_store, snapshot_id,
                           NID, "lessons", "Compressed.", TODAY)
self.assertEqual(memory.read_bytes(), original_memory)
self.assertFalse(expected_l3_path.exists())
```

- [ ] **Step 2: Run DISTIL tests and verify RED**

Run:

```bash
cp scripts/memory_note.py scripts/mnemos.py scripts/evidence.py scripts/secretscan.py scripts/snapshot.py /tmp/
python scripts/test_evidence.py
```

Expected: assertion failure that `memory_note.distill` is absent, then behavior failures until the transition exists.

- [ ] **Step 3: Implement `distill`**

`distill` must:

1. read MEMORY once and parse notes with `mnemos.parse_notes`;
2. require exactly one matching full note and validate it through `mnemos.check_notes`;
3. require `mnemos.score_notes(...)[...]["action"] == "DISTIL"`;
4. require `observation.strip()` non-empty and one physical line;
5. build the deterministic L3 heading/path through the same category/slug helper as `create`;
6. load and verify `snapshot_id`, require its `base` to resolve to `root`, require the current MEMORY digest to equal the manifest's `files[MEMORY-relative-path]`, and require the planned L3 relative path in manifest `missing`;
7. replace exactly the current `- **Observation:** ...` value inside that note body;
8. retain every other scalar field and Evidence line byte-for-byte;
9. build the complete L2 stub from parsed confidence, Directive, and path;
10. replace the exact source span using the note's `scan_start`/`scan_end` offsets;
11. apply the same prepared pair-write/rollback sequence as Task 3.

Add the CLI:

```text
memory_note.py distill --memory PATH --root PATH --snapshot-store PATH --snapshot-id SNAP-ID --id MEM-ID --category NAME --observation TEXT --today YYYY-MM-DD
```

Do not add an auto-distil loop to `mnemos.py`, `tick.py`, or `health.py`.

- [ ] **Step 4: Verify DISTIL GREEN and regression safety**

Run:

```bash
cp scripts/*.py /tmp/
python scripts/test_evidence.py
python scripts/test_mnemos.py
python scripts/test_health.py
python scripts/test_recall.py
```

Expected: all commands exit 0. Inspect the temporary test output to confirm the source L2 note became one stub and the L3 file retained two Evidence lines.

- [ ] **Step 5: Commit Task 4**

```bash
git add scripts/memory_note.py scripts/test_evidence.py
git commit -m "feat(memory): preserve evidence through distillation"
```

---

### Task 5: L2/L3 Evidence Health and L3 Index

**Files:**
- Modify: `scripts/graphcheck.py:28-195`
- Modify: `scripts/health.py:98-119`
- Modify: `scripts/test_health.py`
- Modify: `scripts/test_evidence.py`

**Interfaces:**
- Consumes: `evidence.inspect(text, today) -> EvidenceReport`; `MNEMOS_TODAY` environment override.
- Produces: `l3_full_note_id(path: str, text: str) -> str | None`; L2/L3 output lines `[FAIL]` / `[WARN]`; `INDEX-L3.md` columns `S/C` and `State`; health AMBER for contested evidence in either tier.

- [ ] **Step 1: Add failing L3 and health tests**

In `test_evidence.py`, load `/tmp/graphcheck.py`, point its `STAGE` and `MANIFEST` globals at a temporary staged corpus, and capture `main()` stdout. Add four cases:

1. active L3 full note without Evidence → `[FAIL] ... at least one Evidence record`, exit 1;
2. valid SUPPORT note → no evidence finding, `S/C` is `1/0`, state `supported`;
3. SUPPORT + CHALLENGE → `[WARN] ... contested`, `S/C` is `1/1`, state `contested`, exit 1;
4. daily document with no self-ID and an L2 stub → exempt from Evidence validation.
Add direct cases for `l3_full_note_id`: the first heading `# MEM-2026-0001 — Title` returns that ID; an archive path, stub body, daily document without a self-ID, or filename-only ID with no declaring heading returns `None`.

Before changing the finding representation, add characterization fixtures that already pass on the baseline: a dangling L2 stub target remains FAIL, an unresolved L3 `[[MEM-ID]]` remains FAIL, an unreferenced active L3 file remains an orphan/exit 1, and a valid bidirectional L2-stub/L3 pair remains clean. These guard C1, C2, C4, and C5 while the status type changes.

Add an isolated runpy test that stages only `graphcheck.py`, `evidence.py`, and `secretscan.py`; it must load from an unrelated working directory without resolving any source-tree module.

Load `/tmp/health.py` through the guarded helper in `test_health.py`. Add interface guards so missing helpers fail by assertion, then test:

```python
def test_evidence_warnings_are_classified_without_red(self):
    self.assertTrue(hasattr(health, "classified_findings"),
                    "classified_findings is not implemented")
    fails, warnings = health.classified_findings(
        "  [WARN] MEM-2026-0001 contested evidence\n"
    )
    self.assertEqual(fails, [])
    self.assertEqual(len(warnings), 1)

    self.assertTrue(hasattr(health, "cross_tier_findings"),
                    "cross_tier_findings is not implemented")
    fails, warnings, orphans = health.cross_tier_findings(
        "  [WARN] /Memory/context/x.md contested evidence\n"
    )
    self.assertEqual((fails, len(warnings), orphans), ([], 1, []))
```

Add a staged `health.main()` fixture with a contested L2 note; assert the L2 row is AMBER and output does not contain `unexpected exit 1` or a contested-evidence RED.

- [ ] **Step 2: Run graph/health tests and verify RED**

Run:

```bash
cp scripts/graphcheck.py scripts/health.py scripts/evidence.py scripts/secretscan.py /tmp/
python scripts/test_evidence.py
python scripts/test_health.py
```

Expected: missing L3 Evidence is ignored, generated `INDEX-L3.md` lacks `S/C`, and both health classification helpers are absent.

- [ ] **Step 3: Integrate Evidence into `graphcheck.py`**

Anchor/import `evidence`. Parse `today` from `MNEMOS_TODAY`, defaulting to `date.today()`. Implement `l3_full_note_id` once: return the ID declared by the first Markdown heading only when the path is non-archive and the body is not a stub; otherwise return `None`. Use this helper for every L3 Evidence decision and expose it to the migration planner—do not duplicate the convention there.

Represent finding status explicitly (`PASS`, `WARN`, `FAIL`) instead of overloading a boolean. For every full note, map `EvidenceReport` findings to its real path. Store the report by path for index generation.

Extend `INDEX-L3.md`:

```markdown
| File | Category | Reached by | S/C | State |
|---|---|---|---|---|
```

State is `supported`, `contested`, or `-` for exempt documents. Return 1 for either FAIL or WARN, 0 only when no failures, warnings, or orphans exist.

- [ ] **Step 4: Propagate L2 and L3 warnings through `health.py`**

Extract reusable classifiers:

```python
def classified_findings(output: str):
    fails = [line.strip() for line in output.splitlines() if "[FAIL]" in line]
    warnings = [line.strip() for line in output.splitlines() if "[WARN]" in line]
    return fails, warnings

def cross_tier_findings(output: str):
    fails, warnings = classified_findings(output)
    orphans = [line.strip(" !") for line in output.splitlines()
               if line.startswith("  !") and line.strip(" !").startswith("/")]
    return fails, warnings, orphans
```

In the L2 branch, FAIL remains RED; an index mismatch remains regenerable AMBER; WARN produces AMBER plus `evidence:` escalations; GREEN is allowed only when exit 0 and no FAIL/WARN exists. Evaluate index drift and warnings independently so one does not hide the other. A warning-only exit 1 is expected, not `unexpected exit 1`.

In the cross-tier branch, failures/orphans remain RED, warnings are AMBER, and GREEN is allowed only when all three lists are empty. Add warnings to escalations with prefix `evidence:`. Never translate a contested note into RED.

- [ ] **Step 5: Verify L3 and health GREEN/AMBER behavior**

Run the commands from Step 2. Then stage a valid temporary L2/L3 corpus and run `health.py`; expected verdict GREEN. Add one CHALLENGE in L2 and rerun; expected AMBER. Move that challenge to L3 and rerun; expected AMBER with no broken-link RED.

- [ ] **Step 6: Commit Task 5**

```bash
git add scripts/graphcheck.py scripts/health.py scripts/test_health.py scripts/test_evidence.py
git commit -m "feat(memory): audit evidence across tiers"
```

---

### Task 6: Deterministic Migration Dry-Run Planner

**Files:**
- Create: `scripts/evidence_migrate.py`
- Modify: `scripts/test_evidence.py`

**Interfaces:**
- Consumes: `mnemos.parse_notes`, `graphcheck.l3_full_note_id(path, text)`, `evidence.inspect`, `evidence.append`.
- Produces:
  - `MigrationRow(note_id: str, path: str, target_path: str | None, confidence: str, status: str, delta_bytes: int, detail: str)`
  - `MigrationReport(rows: tuple[MigrationRow, ...], projected_l2_lines: int, projected_l2_bytes: int, ready: bool)`
  - `plan(memory_text: str, l3_docs: dict[str, str], decisions: dict[str, dict], today: date) -> MigrationReport`
  - CLI deterministic JSON report; no corpus writes.

**Decision-file contract:**

```json
{
  "schema_version": 1,
  "notes": {
    "MEM-2026-0001": {
      "action": "ADD",
      "evidence": {
        "date": "2026-09-03",
        "stance": "SUPPORT",
        "source": "reopened source path or command",
        "quote": "exact excerpt"
      }
    },
    "MEM-2026-0002": {"action": "ARCHIVE"}
  }
}
```

Only `ADD` and `ARCHIVE` are valid. The planner never derives a decision from existing `Observation` or `Provenance`.

- [ ] **Step 1: Add failing migration-accounting tests**

Load the not-yet-present module without creating an import error:

```python
migration = load_module("evidence_migrate.py")

class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(migration,
                             "evidence_migrate.py is not implemented")
```

Cover:

- one valid note → `READY`, delta 0;
- one missing-Evidence note without decision → `BLOCKED`, report contains its ID/path/confidence;
- an `ADD` decision for an otherwise-valid note whose only defect is missing Evidence → `READY`, positive projected delta, proposed record validated by `evidence.append`;
- an `ARCHIVE` decision with no active incoming links/stub → `ARCHIVE`, deterministic `target_path`, and active L2 projected size excluding the note span;
- unknown decision ID, missing active-note decision, duplicate active self-ID, malformed existing Evidence, invalid proposed Evidence, unsupported action, or ARCHIVE with an unrepaired incoming link/stub → report `ready is False`;
- projected L2 over 200 lines or 12,288 bytes → `ready is False` with exact projected values;
- every active L2 full note and L3 self-ID appears exactly once; stubs, daily docs, and archive paths do not;
- output ordering is stable by `(note_id, path)` and two runs are byte-identical;
- no generated report contains an invented quote or source.

Add an isolated runpy test that stages `evidence_migrate.py`, `mnemos.py`, `graphcheck.py`, `evidence.py`, and `secretscan.py` and loads the migration module from an unrelated working directory.

- [ ] **Step 2: Run migration tests and verify RED**

Run:

```bash
rm -f /tmp/evidence_migrate.py
cp scripts/mnemos.py scripts/graphcheck.py scripts/evidence.py scripts/secretscan.py /tmp/
python scripts/test_evidence.py
```

Expected: the test's guarded loader reports `evidence_migrate.py is not implemented` as an assertion failure.

- [ ] **Step 3: Implement the pure planner and report renderer**

`plan` must parse every L2 note, skip stubs, scan each supplied non-archive L3 Markdown body for a self-ID, and reject duplicate active IDs. For each full note:

- valid ledger → `READY`, delta 0;
- absent ledger + valid `ADD` decision → validate a projected `evidence.append`, report its byte delta;
- malformed existing ledger → `BLOCKED` until manually repaired or explicitly archived; never append past corruption;
- absent or malformed ledger + `ARCHIVE` decision → `ARCHIVE`; mark the whole report not ready while active incoming links or an L2 stub still target it;
- absent ledger + no decision → `BLOCKED`;
- valid ledger + decision → fail as a stale/unnecessary decision rather than silently applying it.

For ARCHIVE, set `target_path` deterministically: an L2 full note maps to `Memory/_archive/evidence-migration/l2/{note-id}-{slug}.md`; an L3 note preserves its path below `Memory/` under `Memory/_archive/evidence-migration/`. Refuse a duplicate target. Non-ARCHIVE rows use `target_path=None`.

For an L2 `ADD`, call `evidence.append(note["body"], ...)`, then splice that projected body into `memory_text[note["body_start"]:note["scan_end"]]`; never append an Evidence line to the end of the whole MEMORY file. For L3, append to that file's full text. Use the resulting projected MEMORY text—not arithmetic estimates—to calculate UTF-8 bytes and `splitlines()` count.
Collect all L2 ADD replacements and ARCHIVE removals against original offsets and apply them in descending start-offset order so an earlier size change cannot invalidate a later note span.

Compute projected L2 bytes/lines from the projected ADD text and removal of ARCHIVE note spans. The planner reports cap overflow; it does not choose which note to demote.

The CLI accepts:

```text
evidence_migrate.py --memory PATH --manifest PATH --decisions PATH --output PATH --today YYYY-MM-DD
```

The manifest maps staged filenames to real paths. Load active `Memory/**/*.md`, excluding `_archive`, `INDEX.md`, `INDEX-L3.md`, and `PROTOCOL.md`. Write deterministic JSON with sorted keys and a final newline. Exit 0 only when `report.ready` is true, 1 for a complete blocked report, and 2 for I/O/JSON/manifest fatal errors. Never write MEMORY or L3 files.

- [ ] **Step 4: Verify migration planner GREEN and CLI immutability**

Run `test_evidence.py`. Then invoke the CLI against a temporary corpus, hash all inputs before and after, and assert hashes are identical while the report file is created. Run twice and compare report bytes.

- [ ] **Step 5: Commit Task 6**

```bash
git add scripts/evidence_migrate.py scripts/test_evidence.py
git commit -m "feat(memory): plan evidence schema migration"
```

---

### Task 7: Runtime, Package, Recall, and Documentation Cutover

**Files:**
- Modify: `scripts/test_recall.py`
- Modify: `scripts/doctor.py`
- Modify: `autonomy/config/health-scope.json`
- Modify: `autonomy/config/invariants.json`
- Modify: `autonomy/config/regression.json`
- Modify: `verify_kit.py`
- Modify: `templates/root/MEMORY.md`
- Modify: `templates/root/AGENTS.md`
- Modify: `templates/skills/memory.md`
- Modify: `templates/memory/INDEX.md`
- Modify: `templates/memory/INDEX-L3.md`
- Modify: `docs/MEMORY-PROTOCOL.md`
- Modify: `docs/MNEMOS-CURRENT-STATE.md`
- Modify: `docs/MNEMOS-IMPLEMENTATION-GUIDE.md`
- Modify: `INSTALL.md`
- Modify: `PACKAGE-STATUS.md`

**Interfaces:**
- Consumes: completed Tasks 1–6.
- Produces: mandatory suite 12, complete flat-staging instructions, immutable/attested evidence enforcement, canonical operator docs/templates.

- [ ] **Step 1: Add a retrieval invariance test before changing integration files**

Add to `test_recall.py` an evidence-bearing L3 note whose unique query term occurs only in `quote`; assert recall returns that note. Production `recall.py` must remain untouched—review `git diff -- scripts/recall.py` after the test—to enforce that support/challenge counts did not become a ranking channel. Existing recall ranking tests remain the regression for score behavior.

Run `python scripts/test_recall.py`; expected PASS before integration because production recall needs no change.

- [ ] **Step 2: Register and protect the new enforcement module**

First make only the declarative changes:

- `health-scope.json`: add `/scripts/evidence.py` to `required` and `tool_inputs`; `include /scripts/*.py` already covers migration/tests.
- `invariants.json`: add `scripts/evidence.py` beside `scripts/secretscan.py`.
- `regression.json`: append `{ "id": "evidence_distil", "script": "scripts/test_evidence.py", "args": [] }` as entry 12.

Run `python verify_kit.py` before changing its fixed count. Expected: its static checks reach the regression gate and report `expected 11 regression suites, found 12` in addition to any separately identified Windows-baseline self-test failures. Then change `verify_kit.py`'s expected count/message from 11 to 12. Add `scripts/test_evidence.py` to `scanner_sources` only if the real generic pass flags a synthetic fixture; do not pre-emptively widen the exclusion.

Update `doctor.py` to name/stage `evidence.py`, `secretscan.py`, `memory_note.py`, `snapshot.py`, and `test_evidence.py`; append `test_evidence.py` to `SELFTESTS`.

- [ ] **Step 3: Update runtime templates**

`templates/root/MEMORY.md` must show the canonical Evidence JSON line in the Atomic Notes comments and state that every full note needs at least one SUPPORT. Keep the 200-line/12,288-byte caps unchanged.

`templates/root/AGENTS.md` and `templates/skills/memory.md` must name `evidence.py` and `secretscan.py` for memory/graph audits, plus `snapshot.py` for `memory_note.py distill`; migration instructions stage `evidence_migrate.py`, `mnemos.py`, `graphcheck.py`, `evidence.py`, and `secretscan.py` together. `/memory update` requires a source-verifiable SUPPORT record. `/memory prune` snapshots first, passes the resulting snapshot ID into DISTIL, preserves the ledger, and treats CHALLENGE as contested operator review. Remove the old “compress to one line and drop trace” instruction.

The runtime instructions must also state: append-only evidence never rewrites a Claim; a material Claim/Directive change or a seventeenth record creates a successor note linked to its preserved predecessor.

Update both initial index templates to the generated headers with `S/C`; L3 also includes `State`.

- [ ] **Step 4: Update canonical docs without rewriting historical evidence**

- `MEMORY-PROTOCOL.md`: replace eleven-field schema with the twelfth repeatable Evidence field; define SUPPORT/CHALLENGE, derived `S/C`, bounds, and non-lossy DISTIL.
- `MNEMOS-CURRENT-STATE.md`: add the implemented evidence module, mandatory full-note rule, contested semantics, suite 12, migration planner, and new generated index fields. Preserve dated statements that the original ten suites passed before authoring.
- `MNEMOS-IMPLEMENTATION-GUIDE.md`: keep its 2026-08-29 eleven-field text as historical; add a clearly dated 2026-09-03 amendment pointing to the approved spec and current protocol.
- `INSTALL.md` / `PACKAGE-STATUS.md`: use twelve-suite language, list required staged dependencies, and record only verification actually observed in Task 8.

- [ ] **Step 5: Run configuration, docs, and focused suite checks**

Run:

```bash
python -c "import json; from pathlib import Path; paths=[Path('autonomy/config/health-scope.json'),Path('autonomy/config/invariants.json'),Path('autonomy/config/regression.json')]; [json.loads(path.read_text(encoding='utf-8')) for path in paths]; print('JSON OK')"
cp scripts/*.py /tmp/
python scripts/test_evidence.py
python scripts/test_mnemos.py
python scripts/test_health.py
python scripts/test_recall.py
python scripts/test_secretscan.py
```

Expected: all commands exit 0 and regression suite length is 12. Use the repository Grep tool with pattern `eleven-suite|eleven mandatory|11/11 bundled`; only explicitly dated historical statements may remain, never live-contract wording.

- [ ] **Step 6: Commit Task 7**

```bash
git add scripts/test_recall.py scripts/doctor.py autonomy/config/health-scope.json autonomy/config/invariants.json autonomy/config/regression.json verify_kit.py templates/root/MEMORY.md templates/root/AGENTS.md templates/skills/memory.md templates/memory/INDEX.md templates/memory/INDEX-L3.md docs/MEMORY-PROTOCOL.md docs/MNEMOS-CURRENT-STATE.md docs/MNEMOS-IMPLEMENTATION-GUIDE.md INSTALL.md PACKAGE-STATUS.md
git commit -m "docs(memory): cut over to evidence-bearing notes"
```

---

### Task 8: End-to-End Verification and Implemented Status

**Files:**
- Modify after proof only: `docs/specs/2026-09-03-mnemos-evidence-accumulating-distil-design.md`
- Modify after proof only: `PACKAGE-STATUS.md`

**Interfaces:**
- Consumes: all previous tasks.
- Produces: fresh behavioral evidence, installed-package evidence, and truthful implementation status.

- [ ] **Step 1: Run the complete focused Windows gate**

Stage every sibling module first, then run:

```bash
cp scripts/*.py /tmp/
python scripts/test_evidence.py
python scripts/test_mnemos.py
python scripts/test_health.py
python scripts/test_handoff.py
python scripts/test_autonomy_dispatcher.py
python scripts/test_recall.py
python scripts/test_vecidx.py
python scripts/test_secretscan.py
python scripts/recall.py --selftest
```

Expected: every command exits 0. Count and report commands exactly; do not summarize a partial run as the full focused gate.

- [ ] **Step 2: Smoke the actual CLI lifecycle**

In one real temporary workspace:

1. create a valid evidence-bearing L3 note through `memory_note.py create`;
2. append one SUPPORT and one CHALLENGE through `append-evidence`;
3. run graph audit and observe `S/C = 2/1`, state `contested`, nonzero findings exit;
4. create a low-utility evidence-bearing L2 note;
5. snapshot it;
6. run `memory_note.py distill`;
7. run `mnemos.py`, `graphcheck.py`, and recall;
8. observe one valid L2 stub, one L3 full note, every Evidence record preserved, and successful retrieval by a quote term.

Capture exit codes and the relevant verdict/index lines. Confirm no complete fake secret appears in any output.

- [ ] **Step 3: Smoke the migration planner**

Run one blocked corpus without decisions and one ready corpus with explicit ADD/ARCHIVE decisions. Verify deterministic reports, exact note accounting, projected L2 lines/bytes, and byte-identical input hashes before/after.

- [ ] **Step 4: Verify a clean installed package**

Install into a deterministic empty target outside the repository:

```bash
python -c "import shutil; shutil.rmtree(r'C:/tmp/mnemos-evidence-install-smoke', ignore_errors=True)"
python install.py --target C:/tmp/mnemos-evidence-install-smoke
cp C:/tmp/mnemos-evidence-install-smoke/scripts/*.py /tmp/
python C:/tmp/mnemos-evidence-install-smoke/scripts/test_evidence.py
python C:/tmp/mnemos-evidence-install-smoke/scripts/test_mnemos.py
python C:/tmp/mnemos-evidence-install-smoke/scripts/test_health.py
python C:/tmp/mnemos-evidence-install-smoke/scripts/test_recall.py
```

Confirm `scripts/evidence.py`, `scripts/evidence_migrate.py`, and `scripts/test_evidence.py` exist in that target. Expected: install exits 0 and all four installed suites exit 0.

- [ ] **Step 5: Run package verification honestly**

Run `python verify_kit.py`.

On the target SiemensGPT/POSIX runtime, expected: required files, Python/JSON parsing, generic credential scan, and all 12 mandatory suites pass; final result PASS.

On this Windows workstation, record the exact output. The known unrelated baseline may still fail snapshot/tick/evolution and occasionally health file replacement. The evidence/DISTIL suite, static package checks, and focused commands must pass. Do not change those unrelated modules or call the package verifier green when its exit is nonzero.

- [ ] **Step 6: Update status only to the level proved**

If the target-runtime 12-suite gate passed, change the design status to `Implemented and verified` and record the commands/date in `PACKAGE-STATUS.md`. If only focused Windows/install verification ran, use `Implemented; target-runtime 12-suite verification pending` and list the exact remaining external gate. Never convert the August historical evidence into a September claim.

- [ ] **Step 7: Check final patch and commit status evidence**

Run:

```bash
git diff --check
git status --short
```

Review every remaining changed file against the spec acceptance criteria. Then commit only the status evidence:

```bash
git add docs/specs/2026-09-03-mnemos-evidence-accumulating-distil-design.md PACKAGE-STATUS.md
git commit -m "docs(memory): record evidence distillation verification"
```
