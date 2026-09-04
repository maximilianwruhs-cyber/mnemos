# MNEMOS Semantic Recall Baseline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a reproducible train/dev quality baseline that compares current and Unicode BM25, selects a bounded BM25/Potion candidate policy, gates candidate recall, and records zero-shot mmarco reranking without opening certification.

**Architecture:** A standalone evaluator imports the existing corpus, recall, and vector modules but never changes the production search path. Pure ranking/metric functions remain testable without model dependencies; a small ONNX adapter and a separate online fetch command own the optional model boundary. Canonical config, policy, reports, and manifests make every result attributable and drift-detectable.

**Tech Stack:** Python 3.12 stdlib (`argparse`, `dataclasses`, `hashlib`, `json`, `random`, `statistics`, `urllib`), existing `recall.py` / `vecidx.py` / `corpus_lint.py`, numpy 2.1.3, model2vec 0.9.0, tokenizers 0.21.0, onnxruntime 1.20.1, `unittest`.

**Spec:** `docs/superpowers/specs/2026-09-04-mnemos-semantic-recall-baseline-design.md`

## Global Constraints

- Baseline-v1 loads only `train` and `dev` through `corpus_lint.load_split(..., certified_run=False)`; no baseline config or command names the certification path.
- Corpus version stays `v2`; its six data files and corpus hash `f71199596a8de64b748287211d34f26b4645c74d3e410afc11c86b527a1f8ba3` remain byte-identical.
- Candidate cap is exactly 20. Only B1 is a gate: dev overall any-gold Recall@20 ≥99% (at least 155/156), safety Recall@20 100%, identifier Recall@20 100%, zero protected-hit loss.
- B0-current preserves `recall.tokens`; B0-unicode changes tokenization only inside the evaluator. Production `recall.search()` remains unchanged.
- B2 is zero-shot and descriptive: no training, threshold, abstention, label leakage, or post-dev adjustment.
- Model scoring receives only `query.text` and `note.text`; IDs exist only for joins and deterministic ties.
- Model download is an explicit setup command. Evaluation contains no download/network path.
- All baseline artifacts are canonical UTF-8 JSON with LF and a trailing newline. Any changed input/config/model/metric definition creates `baseline-v2`.
- Windows timing is diagnostic. This plan does not satisfy the Linux performance certification gate.
- Tests use the repository's standalone `unittest` convention; no pytest dependency.

---

## File Structure

- Modify `scripts/corpus_lint.py` — emit and verify the corpus freeze marker.
- Modify `scripts/test_corpus_lint.py` — pin freeze-marker behavior.
- Modify `scripts/fixtures/vector-semantics/corpus-v2/manifest.json` — add `"frozen": true` without changing corpus hashes.
- Modify `.gitattributes` — pin baseline artifacts to LF.
- Create `requirements-semantic-baseline.txt` — exact direct dependency pins.
- Create `scripts/fixtures/vector-semantics/baseline-v1/config.json` — immutable baseline inputs and policy grid.
- Create `scripts/semantic_baseline_core.py` — pure corpus adapter, B0/B1 policy, metric, bootstrap, and canonical-JSON logic.
- Create `scripts/semantic_baseline.py` — preflight, train/dev state machine, artifact checks, evidence rendering, and CLI.
- Create `scripts/semantic_onnx.py` — lazy ONNX/tokenizer boundary and protected-prefix reranking.
- Create `scripts/fetch_reranker.py` — revision-pinned, hash-checking online setup.
- Create `scripts/test_semantic_baseline.py` — pure behavior and CLI/state tests.
- Create `scripts/test_semantic_onnx.py` — model-boundary tests with fakes; real smoke skips until artifact/runtime exist.
- Generate `scripts/fixtures/vector-semantics/baseline-v1/model-manifest.json` — verified local model/package identity.
- Generate `scripts/fixtures/vector-semantics/baseline-v1/selected-policy.json` — frozen train-selected tokenizer/quota.
- Generate `scripts/fixtures/vector-semantics/baseline-v1/train-report.json` — B0 + all 14 B1 policies.
- Generate `scripts/fixtures/vector-semantics/baseline-v1/dev-report.json` — frozen-policy B0/B1 and, after B1 PASS, B2.
- Generate `scripts/fixtures/vector-semantics/baseline-v1/manifest.json` — hashes of all baseline artifacts.
- Generate `docs/wayfinder/mnemos-semantic-recall-release-decision/evidence/semantic-baseline-v1.md` — report-derived evidence.
- Modify `docs/wayfinder/mnemos-semantic-recall-release-decision/tickets/establish-production-candidate-recall.md` — resolve with measured outcome.
- Modify `docs/wayfinder/mnemos-semantic-recall-release-decision/MAP.md` — record the baseline/candidate decision.

---

### Task 1: Enforce the corpus freeze marker

**Files:**
- Modify: `scripts/corpus_lint.py:396-429`
- Modify: `scripts/test_corpus_lint.py:196-210`
- Modify: `scripts/fixtures/vector-semantics/corpus-v2/manifest.json`

**Interfaces:**
- Consumes: existing `compute_manifest(data, corpus_version) -> dict` and `check_manifest(root, computed) -> list[str]`.
- Produces: every computed/emitted manifest contains `"frozen": true`; `--check` rejects missing/false values.

- [ ] **Step 1: Add failing freeze-marker tests**

Extend `test_manifest_check_roundtrips_and_detects_drift` and add a focused missing-marker test:

```python
_, manifest = corpus_lint.lint(root, enforce_floors=False)
self.assertIs(manifest["frozen"], True)

(root / corpus_lint.MANIFEST).write_text(
    json.dumps({k: v for k, v in manifest.items() if k != "frozen"}, sort_keys=True) + "\n",
    encoding="utf-8",
)
self.assertIn(
    "manifest.json frozen mismatch (declared != recomputed)",
    corpus_lint.check_manifest(root, manifest),
)
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
python scripts/test_corpus_lint.py CorpusLintTests.test_manifest_check_roundtrips_and_detects_drift -v
```

Expected: failure because `manifest["frozen"]` is absent.

- [ ] **Step 3: Emit and require the marker**

Change `compute_manifest` and `check_manifest`:

```python
def compute_manifest(data, corpus_version) -> dict:
    file_hashes = {}
    counts = {}
    for split in SPLITS:
        counts[split] = {
            "notes": len(data["notes"][split]),
            "queries": len(data["queries"][split]),
        }
        for kind in ("notes", "queries"):
            rel = f"{split}/{kind}.jsonl"
            file_hashes[rel] = hashlib.sha256(canonical_bytes(data[kind][split])).hexdigest()
    rollup = "\n".join(f"{file_hashes[rel]}  {rel}" for rel in sorted(file_hashes))
    return {
        "corpus_version": corpus_version,
        "frozen": True,
        "counts": counts,
        "file_hashes": file_hashes,
        "corpus_hash": hashlib.sha256(rollup.encode("utf-8")).hexdigest(),
    }
```

```python
for key in ("corpus_version", "frozen", "counts", "file_hashes", "corpus_hash"):
    if declared.get(key) != computed[key]:
        errors.append(f"{MANIFEST} {key} mismatch (declared != recomputed)")
```

- [ ] **Step 4: Run corpus tests and re-emit the manifest**

Run:

```bash
python scripts/test_corpus_lint.py
python scripts/corpus_build.py --emit-manifest
python scripts/corpus_build.py --check
```

Expected: 19+ tests pass; both corpus commands report hash
`f71199596a8de64b748287211d34f26b4645c74d3e410afc11c86b527a1f8ba3`; manifest contains
`"frozen": true`.

- [ ] **Step 5: Prove no corpus data hash moved**

Run:

```bash
python -c "import json,pathlib; p=pathlib.Path('scripts/fixtures/vector-semantics/corpus-v2/manifest.json'); m=json.loads(p.read_text(encoding='utf-8')); assert m['frozen'] is True; assert m['corpus_hash']=='f71199596a8de64b748287211d34f26b4645c74d3e410afc11c86b527a1f8ba3'; print('freeze invariant: PASS')"
```

Expected: `freeze invariant: PASS`.

- [ ] **Step 6: Commit the invariant repair**

```bash
git add scripts/corpus_lint.py scripts/test_corpus_lint.py scripts/fixtures/vector-semantics/corpus-v2/manifest.json
git commit -m "fix(corpus): enforce frozen manifest marker"
```

---

### Task 2: Build the deterministic lexical foundation

**Files:**
- Create: `scripts/semantic_baseline.py`
- Create: `scripts/test_semantic_baseline.py`
- Create: `scripts/fixtures/vector-semantics/baseline-v1/config.json`
- Modify: `.gitattributes`

**Interfaces:**
- Consumes: `corpus_lint.load_split`, `recall.tokens`, `recall.bm25`, and `recall._normalise`.
- Produces:
  - `class BaselineError(ValueError)` for invalid policy/data invariants.
  - `class SetupError(BaselineError)` for missing or mismatched runtime artifacts.
  - `unicode_tokens(text: str) -> list[str]`
  - `adapt_notes(notes: list[dict], tokenizer: Callable[[str], list[str]]) -> dict[str, dict]`
  - `rank_bm25(query_text: str, notes: list[dict], tokenizer: Callable[[str], list[str]]) -> list[tuple[str, float]]`
  - `identifier_tokens(text: str) -> tuple[str, ...]`
  - `protected_note_ids(query_text: str, notes: list[dict], deterministic: list[tuple[str, float]]) -> tuple[str, ...]`
  - immutable baseline-v1 config.

- [ ] **Step 1: Write RED tests for B0-current, B0-unicode, and ID leakage**

Create `scripts/test_semantic_baseline.py` using the existing `unittest` import pattern. Pin these behaviors:

```python
class LexicalBaselineTests(unittest.TestCase):
    def test_unicode_tokens_keep_german_words_whole(self):
        self.assertEqual(
            baseline.unicode_tokens("Prüfen für größere Schlüssel"),
            ["prüfen", "für", "grössere", "schlüssel"],
        )

    def test_current_ascii_behavior_remains_visible(self):
        self.assertNotIn("prüfen", recall.tokens("Prüfen"))

    def test_note_id_never_enters_search_tokens(self):
        notes = [{"id": "n-secret-widget", "text": "unrelated body"}]
        docs = baseline.adapt_notes(notes, recall.tokens)
        self.assertNotIn("secret", docs["n-secret-widget"]["tokens"])
        self.assertNotIn("widget", docs["n-secret-widget"]["tokens"])

    def test_zero_overlap_has_no_arbitrary_candidate(self):
        notes = [{"id": "n-a", "text": "alpha beta"}]
        self.assertEqual(baseline.rank_bm25("unseen", notes, recall.tokens), [])
```

- [ ] **Step 2: Run the focused tests and verify RED**

```bash
python scripts/test_semantic_baseline.py LexicalBaselineTests -v
```

Expected: import/function failures because the evaluator does not exist.

- [ ] **Step 3: Implement Unicode tokenization and text-only BM25**

At module scope:

```python
class BaselineError(ValueError):
    pass


class SetupError(BaselineError):
    pass
```

```python
UNICODE_TOKEN_RE = re.compile(r"[^\W_][\w.\-]*", re.UNICODE)


def unicode_tokens(text: str) -> list[str]:
    out = []
    for match in UNICODE_TOKEN_RE.finditer(unicodedata.normalize("NFC", text).casefold()):
        token = match.group(0).strip(".-_")
        if len(token) >= 2 and token not in recall.STOP:
            out.append(token)
    return out
```

Build neutral docs without title/ID leakage and rank only positive BM25 scores:

```python
def adapt_notes(notes, tokenizer):
    return {
        note["id"]: {
            "path": note["id"], "title": "", "text": note["text"],
            "created": None, "salience": recall.DEFAULT_SALIENCE,
            "mentions": set(), "self_ids": set(),
            "tokens": tokenizer(note["text"]),
        }
        for note in notes
    }


def rank_bm25(query_text, notes, tokenizer):
    docs = adapt_notes(notes, tokenizer)
    query_tokens = tokenizer(query_text)
    if not query_tokens:
        return []
    scores = recall._normalise(recall.bm25(docs, query_tokens))
    return sorted(
        ((note_id, score) for note_id, score in scores.items() if score > 0.0),
        key=lambda item: (-item[1], item[0]),
    )
```

- [ ] **Step 4: Add RED tests for identifier classification and protection**

```python
class IdentifierProtectionTests(unittest.TestCase):
    def test_identifier_tokens_include_codes_paths_and_flags(self):
        self.assertEqual(
            baseline.identifier_tokens("Use KX-4471, EVT.908 and --offline, not API"),
            ("--offline", "evt.908", "kx-4471"),
        )

    def test_hyphenated_natural_word_is_not_identifier(self):
        self.assertEqual(baseline.identifier_tokens("read-only mode"), ())

    def test_protected_notes_follow_deterministic_order_then_id(self):
        notes = [
            {"id": "n-b", "text": "KX-4471 alternate"},
            {"id": "n-a", "text": "KX-4471 exact"},
        ]
        ranked = [("n-b", 0.9)]
        self.assertEqual(
            baseline.protected_note_ids("fix KX-4471", notes, ranked),
            ("n-b", "n-a"),
        )
```

- [ ] **Step 5: Implement exact-identifier protection**

Use one exact ASCII-boundary lexeme regex, then NFC+casefold both query and note text:

```python
IDENTIFIER_LEXEME_RE = re.compile(
    r"(?<![A-Za-z0-9_.:/-])(?:--)?[A-Za-z0-9][A-Za-z0-9_.:/-]*(?![A-Za-z0-9_.:/-])"
)


def _is_identifier(token: str) -> bool:
    return (
        len(token) >= 2
        and (any(ch.isdigit() for ch in token)
             or token.startswith("--")
             or any(ch in token for ch in "._/:"))
    )


def identifier_tokens(text: str) -> tuple[str, ...]:
    normalized = unicodedata.normalize("NFC", text).casefold()
    return tuple(sorted({m.group(0) for m in IDENTIFIER_LEXEME_RE.finditer(normalized)
                         if _is_identifier(m.group(0))}))


def protected_note_ids(query_text, notes, deterministic):
    wanted = set(identifier_tokens(query_text))
    matched = {note["id"] for note in notes if wanted & set(identifier_tokens(note["text"]))}
    positions = {note_id: index for index, (note_id, _score) in enumerate(deterministic)}
    ordered = tuple(sorted(matched, key=lambda note_id: (positions.get(note_id, len(positions)), note_id)))
    if len(ordered) > 20:
        raise BaselineError("identifier_budget_overflow")
    return ordered
```

- [ ] **Step 6: Add the immutable config and LF rule**

Create `baseline-v1/config.json` with:

```json
{
  "baseline_version": "v1",
  "bootstrap": {"samples": 10000, "seed": 20260904},
  "candidate_k": 20,
  "corpus": {
    "corpus_hash": "f71199596a8de64b748287211d34f26b4645c74d3e410afc11c86b527a1f8ba3",
    "corpus_version": "v2",
    "root": "../corpus-v2",
    "splits": ["train", "dev"]
  },
  "dependencies": {
    "model2vec": "0.9.0",
    "numpy": "2.1.3",
    "onnxruntime": "1.20.1",
    "python": "3.12",
    "tokenizers": "0.21.0"
  },
  "lexical_reserves": [20, 16, 12, 10, 8, 4, 0],
  "potion_files": {
    "config.json": "f68ab920d7257faf6cbb4c8da5d96cc41dbbe7842b7043d92f0c2c3d3deef942",
    "model.safetensors": "f65d0f325faadc1e121c319e2faa41170d3fa07d8c89abd48ca5358d9a223de2",
    "modules.json": "0858e4a5e4c99ece0f93eae7660195497a2667a7cfca3dc3223b68df19097056",
    "tokenizer.json": "273ca9e28ec6990aea6206b0364443754d87e87a5dd28e94026ea9999ba3bf62"
  },
  "safety_families": [
    "success-vs-failure", "permit-vs-prohibit", "apply-vs-rollback",
    "online-vs-offline", "current-vs-superseded", "mutate-vs-inspect",
    "cause-vs-coincidence"
  ],
  "tokenizers": ["current_ascii", "unicode_nfc"]
}
```

The block is expanded for review. Write the file through
`json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n"`, so the
first committed form is canonical rather than pretty-printed.

Append:

```gitattributes
scripts/fixtures/vector-semantics/baseline-v1/** text eol=lf
```

- [ ] **Step 7: Run tests and config guard**

```bash
python scripts/test_semantic_baseline.py LexicalBaselineTests IdentifierProtectionTests -v
python scripts/corpus_lint.py scripts/fixtures/vector-semantics/corpus-v2 --check --config scripts/fixtures/vector-semantics/baseline-v1/config.json
```

Expected: all focused tests pass; corpus lint passes and reports the unchanged corpus hash.

- [ ] **Step 8: Commit the lexical foundation**

```bash
git add .gitattributes scripts/semantic_baseline.py scripts/test_semantic_baseline.py scripts/fixtures/vector-semantics/baseline-v1/config.json
git commit -m "feat(recall): add deterministic baseline foundation"
```

---

### Task 3: Add Potion ranking and bounded policy selection

**Files:**
- Modify: `scripts/semantic_baseline.py`
- Modify: `scripts/test_semantic_baseline.py`

**Interfaces:**
- Consumes: Task 2 rankings and protected note IDs; `vecidx.embed_texts`, `vecidx.embed_query`.
- Produces:
  - `policy_grid() -> tuple[tuple[str, int], ...]`
  - `rank_potion(query_text: str, note_ids: list[str], matrix) -> list[tuple[str, float]]`
  - `union_candidates(deterministic, semantic, protected, lexical_reserve, k=20) -> list[str]`
  - `evaluate_candidate_policy(split_data, tokenizer_name, lexical_reserve, semantic_rankings) -> dict`
  - `evaluate_all_candidate_policies(split_data, semantic_rankings) -> list[dict]`
  - `select_policy(policy_reports: list[dict]) -> dict | None`
  - `candidate_gate(report: dict, expected_queries: int | None = None) -> list[str]`.

- [ ] **Step 1: Write RED tests for union semantics**

```python
class CandidateUnionTests(unittest.TestCase):
    def test_dedupes_and_scans_deeper_vector_hits_to_fill_twenty(self):
        lexical = [(f"n-{i:02}", 1.0 - i / 100) for i in range(16)]
        semantic = lexical[:8] + [(f"v-{i:02}", 0.8 - i / 100) for i in range(12)]
        got = baseline.union_candidates(lexical, semantic, (), lexical_reserve=16)
        self.assertEqual(len(got), 20)
        self.assertEqual(got[:16], [note_id for note_id, _ in lexical])

    def test_protected_hits_consume_reserve_and_stay_first(self):
        lexical = [("n-gold", 0.9), ("n-other", 0.8)]
        semantic = [("n-third", 0.95), ("n-gold", 0.7)]
        got = baseline.union_candidates(lexical, semantic, ("n-gold",), lexical_reserve=1, k=3)
        self.assertEqual(got, ["n-gold", "n-third", "n-other"])

    def test_identifier_overflow_fails_instead_of_truncating(self):
        with self.assertRaisesRegex(baseline.BaselineError, "identifier_budget_overflow"):
            baseline.union_candidates([], [], tuple(f"n-{i}" for i in range(21)), 10)
```

- [ ] **Step 2: Run union tests and verify RED**

```bash
python scripts/test_semantic_baseline.py CandidateUnionTests -v
```

Expected: missing `union_candidates` failure.

- [ ] **Step 3: Implement stable union construction**

Implement one append-once helper and count protected items present in deterministic ranking toward
`lexical_reserve`. `20/0` must never read semantic ranks; `0/20` must still retain protected hits.
Backfill only when the preferred fill channel cannot bring the set to K.

- [ ] **Step 4: Write RED tests for all 14 policies and train selection**

Construct synthetic query reports and assert:

```python
def policy_report(tokenizer, reserve, recall, hard_ok=True):
    return {
        "policy_id": f"{tokenizer}-{reserve}-{20 - reserve}",
        "tokenizer": tokenizer,
        "lexical_reserve": reserve,
        "overall": {"hit_at_20": int(recall * 100), "queries": 100},
        "gate_errors": [] if hard_ok else ["safety Recall@20 5/6 != 6/6"],
    }


class PolicySelectionTests(unittest.TestCase):
    def test_grid_is_exactly_fourteen_predeclared_policies(self):
        self.assertEqual(len(baseline.policy_grid()), 14)
        self.assertEqual(baseline.policy_grid()[0], ("current_ascii", 20))
        self.assertEqual(baseline.policy_grid()[-1], ("unicode_nfc", 0))

    def test_selection_prefers_recall_then_current_then_larger_lexical_reserve(self):
        reports = [
            policy_report("unicode_nfc", 16, recall=1.0),
            policy_report("current_ascii", 12, recall=1.0),
            policy_report("current_ascii", 16, recall=1.0),
        ]
        self.assertEqual(baseline.select_policy(reports)["policy_id"], "current_ascii-16-4")

    def test_no_hard_gate_policy_returns_none(self):
        self.assertIsNone(baseline.select_policy([
            policy_report("unicode_nfc", 10, recall=1.0, hard_ok=False),
        ]))
```

- [ ] **Step 5: Implement Potion ranking, policy reports, and gate arithmetic**

`rank_potion` embeds note text only, normalizes through existing `vecidx`, sorts by descending
cosine then note ID, and applies no similarity floor. Candidate recall needs the top semantic
neighbors even when all absolute scores are low.

`candidate_gate` must report exact numerator/denominator strings. Its dev overall boundary is:

```python
required = math.ceil(0.99 * expected_queries)  # 155 when expected_queries == 156
if report["overall"]["hit_at_20"] < required:
    errors.append(f"overall Recall@20 {hits}/{expected_queries} < {required}/{expected_queries}")
```

Safety and identifier denominators come from query labels, never hard-coded counts.

- [ ] **Step 6: Run focused and existing vector tests**

```bash
python scripts/test_semantic_baseline.py CandidateUnionTests PolicySelectionTests -v
python scripts/test_vecidx.py
```

Expected: new focused tests pass; existing vector tests remain green.

- [ ] **Step 7: Commit bounded candidate selection**

```bash
git add scripts/semantic_baseline.py scripts/test_semantic_baseline.py
git commit -m "feat(recall): add bounded baseline candidate policies"
```

---

### Task 4: Add query metrics, clustered confidence intervals, and canonical reports

**Files:**
- Modify: `scripts/semantic_baseline.py`
- Modify: `scripts/test_semantic_baseline.py`

**Interfaces:**
- Consumes: per-query rankings from Tasks 2–3.
- Produces:
  - `score_query(query: dict, ranking: list[str], protected: set[str], notes_by_id: dict[str, dict]) -> dict`
  - `score_rerank(query: dict, ranked_scores: list[tuple[str, float | None]], protected: set[str], notes_by_id: dict[str, dict]) -> dict`
  - `aggregate_results(queries: list[dict], results: dict[str, dict]) -> dict`
  - `cluster_interval(rows: list[dict], metric: str, seed: int, samples: int) -> dict`
  - `paired_cluster_interval(left: list[dict], right: list[dict], metric: str, seed: int, samples: int) -> dict`
  - `canonical_json_bytes(value) -> bytes`
  - `write_json(path: Path, value) -> str` returning SHA-256.

- [ ] **Step 1: Write RED tests for any-of gold, hard negatives, and slices**

```python
class MetricTests(unittest.TestCase):
    def test_any_gold_counts_once_and_records_reciprocal_rank(self):
        query = {
            "id": "q-1", "gold": ["n-en", "n-de"],
            "hard_negatives": [{"note_id": "n-hard", "contrast_family": "permit-vs-prohibit"}],
            "language": "de", "contrast_families": ["permit-vs-prohibit"],
            "provenance": "synthetic-contrast", "scenario_group": "g-1",
        }
        notes = {
            "n-en": {"language": "en"}, "n-de": {"language": "de"},
            "n-hard": {"language": "de"}, "n-x": {"language": "en"},
        }
        got = baseline.score_query(query, ["n-x", "n-de", "n-en"], set(), notes)
        self.assertTrue(got["hit_at_3"])
        self.assertEqual(got["first_gold_rank"], 2)
        self.assertEqual(got["reciprocal_rank"], 0.5)

    def test_hard_negative_top_one_is_explicit(self):
        query = {
            "id": "q-2", "gold": ["n-gold"],
            "hard_negatives": [{"note_id": "n-hard", "contrast_family": "success-vs-failure"}],
            "language": "en", "contrast_families": ["success-vs-failure"],
            "provenance": "synthetic-contrast", "scenario_group": "g-2",
        }
        notes = {"n-gold": {"language": "en"}, "n-hard": {"language": "en"}}
        got = baseline.score_query(query, ["n-hard", "n-gold"], set(), notes)
        self.assertEqual(got["top1_hard_negative"], "n-hard")

    def test_rerank_margins_ignore_protected_none_scores(self):
        query = {
            "id": "q-3", "gold": ["n-gold"],
            "hard_negatives": [{"note_id": "n-hard", "contrast_family": "permit-vs-prohibit"}],
            "language": "en", "contrast_families": ["permit-vs-prohibit"],
            "provenance": "synthetic-contrast", "scenario_group": "g-3",
        }
        notes = {note_id: {"language": "en"} for note_id in ("n-protected", "n-gold", "n-hard")}
        got = baseline.score_rerank(
            query,
            [("n-protected", None), ("n-gold", 2.5), ("n-hard", 1.0)],
            {"n-protected"}, notes,
        )
        self.assertEqual(got["semantic_top1_top2_margin"], 1.5)
        self.assertEqual(got["semantic_top1_best_hard_margin"], 1.5)
```
- [ ] **Step 2: Write RED tests for scenario-cluster bootstrap**

```python
class BootstrapTests(unittest.TestCase):
    def test_resamples_groups_not_individual_paraphrases(self):
        rows = [
            {"scenario_group": "a", "correct": 1.0},
            {"scenario_group": "a", "correct": 1.0},
            {"scenario_group": "b", "correct": 0.0},
        ]
        first = baseline.cluster_interval(rows, "correct", seed=7, samples=1000)
        second = baseline.cluster_interval(rows, "correct", seed=7, samples=1000)
        self.assertEqual(first, second)
        self.assertEqual(first["groups"], 2)
```

- [ ] **Step 3: Implement per-query and aggregate metrics**

Every per-query record must contain `query_id`, `scenario_group`, `language`, families,
provenance, ranking IDs, gold ranks, hard-negative ranks, protected IDs, and booleans for each K.
Aggregate from those records; do not maintain a second independent count path.

`score_rerank` delegates rank-based fields to `score_query`, then keeps numeric logits only.
`semantic_top1_top2_margin` is the first numeric logit minus the second; it is `null` with fewer
than two scored candidates. `semantic_top1_best_hard_margin` is the first numeric logit minus the
highest-scoring labeled hard negative present in B1; it is `null` when no scored hard negative is
present. Protected prefix entries carry score `null` and never enter either diagnostic margin.

Define B2 eligibility exactly as:

```python
ranking = [note_id for note_id, _score in ranked_scores]
eligible = len([note_id for note_id in ranking if note_id not in protected]) >= 2 and not any(
    note_id in query["gold"] for note_id in protected
)
```
- [ ] **Step 4: Implement deterministic cluster bootstrap**

Group rows by `scenario_group`, assert paired systems have identical group IDs, then sample group
IDs with replacement using `random.Random(seed)`. Include every row of each sampled group and
compute 10,000 sample statistics. After sorting, use
`floor(0.025 * (samples - 1))` and `ceil(0.975 * (samples - 1))` as the fixed lower/upper indices.
Paired deltas reuse the same sampled group indices for both systems.

- [ ] **Step 5: Implement canonical output and drift checks**

```python
def canonical_json_bytes(value) -> bytes:
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def write_json(path, value):
    payload = canonical_json_bytes(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return hashlib.sha256(payload).hexdigest()
```

The atomic write must preserve exactly the bytes returned by `canonical_json_bytes`; tests compare
the file bytes and returned hash directly.

- [ ] **Step 6: Run all pure baseline tests**

```bash
python scripts/test_semantic_baseline.py
```

Expected: all lexical, identifier, candidate, metric, bootstrap, and canonical-output tests pass.

- [ ] **Step 7: Commit metrics and report primitives**

```bash
git add scripts/semantic_baseline.py scripts/test_semantic_baseline.py
git commit -m "feat(recall): add baseline metrics and canonical reports"
```

---

### Task 5: Add pinned model provisioning and ONNX reranking

**Files:**
- Create: `requirements-semantic-baseline.txt`
- Create: `scripts/fetch_reranker.py`
- Create: `scripts/semantic_onnx.py`
- Create: `scripts/test_semantic_onnx.py`
- Modify: `scripts/fixtures/vector-semantics/baseline-v1/config.json`

**Interfaces:**
- Consumes: pinned model section from config and candidate IDs from Task 3.
- Produces:
  - `fetch_reranker.fetch_files(model_spec: dict, destination: Path, opener=urlopen) -> dict`
  - `class semantic_onnx.OnnxSetupError(RuntimeError)` for model/runtime boundary failures.
  - `semantic_onnx.verify_environment(config: dict, model_dir: Path) -> dict`
  - `semantic_onnx.OnnxReranker(tokenizer, session, max_length: int)` for dependency-injected tests.
  - `semantic_onnx.OnnxReranker.load(model_dir: Path, max_length: int) -> OnnxReranker`
  - `OnnxReranker.score(query: str, passages: list[str], batch_size: int = 16) -> list[float]`
  - `semantic_onnx.rerank(query, candidate_ids, note_texts, protected_ids, scorer) -> list[tuple[str, float | None]]`.

- [ ] **Step 1: Add exact direct dependency pins**

Create:

```text
# Optional quality-baseline environment; MNEMOS core remains stdlib-only.
numpy==2.1.3
model2vec==0.9.0
onnxruntime==1.20.1
tokenizers==0.21.0
```

- [ ] **Step 2: Add the pinned reranker config**

Add this object to `baseline-v1/config.json`:

```json
{
  "reranker": {
    "max_length": 512,
    "model_dir": ".cache/semantic-baseline-v1/mmarco",
    "provider": "CPUExecutionProvider",
    "repository": "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1",
    "revision": "1427fd652930e4ba29e8149678df786c240d8825",
    "files": {
      "config.json": {"bytes": 891, "sha256": "cc2cfe51aa3fd759d21d21acf5dfd6994aa67a3c9210636d22e143699d336c77"},
      "onnx/model_quint8_avx2.onnx": {"bytes": 118620016, "sha256": "6c2513767fb63d008a4377bef7a7a3555433d9436342bb53e35a3a72ffc52d4b"},
      "special_tokens_map.json": {"bytes": 239, "sha256": "378eb3bf733eb16e65792d7e3fda5b8a4631387ca04d2015199c4d4f22ae554d"},
      "tokenizer.json": {"bytes": 17082660, "sha256": "62c24cdc13d4c9952d63718d6c9fa4c287974249e16b7ade6d5a85e7bbb75626"},
      "tokenizer_config.json": {"bytes": 435, "sha256": "e7fbfbfa6347b4e414c1cee50d142e2c2f9a895dad68b068ae83a8b564c3837e"}
    }
  }
}
```

Merge this object into the existing config, preserve all other keys, and rewrite the complete file
with the canonical JSON encoding established in Task 2. Resolve `model_dir` from the repository
root, not from the caller's working directory.

- [ ] **Step 3: Write RED fetch/hash tests**

Use an injected opener returning `io.BytesIO` so tests never use network:

```python
class FetchTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="fetchcase_", dir="/tmp"))
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))

    def test_fetch_writes_only_verified_bytes(self):
        payload = b"model-bytes"
        model_spec = {
            "repository": "owner/model", "revision": "abc123",
            "files": {"model.onnx": {
                "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest(),
            }},
        }
        manifest = fetch_reranker.fetch_files(
            model_spec, self.root, opener=lambda _: io.BytesIO(payload),
        )
        self.assertEqual((self.root / "model.onnx").read_bytes(), payload)
        self.assertEqual(
            manifest["files"]["model.onnx"]["sha256"],
            model_spec["files"]["model.onnx"]["sha256"],
        )

    def test_hash_mismatch_leaves_no_partial_file(self):
        model_spec = {
            "repository": "owner/model", "revision": "abc123",
            "files": {"model.onnx": {"bytes": 3, "sha256": "0" * 64}},
        }
        with self.assertRaisesRegex(ValueError, "sha256"):
            fetch_reranker.fetch_files(
                model_spec, self.root, opener=lambda _: io.BytesIO(b"bad"),
            )
        self.assertFalse((self.root / "model.onnx").exists())
```

- [ ] **Step 4: Implement revision-pinned fetch and artifact manifest**

Construct each URL as:

```python
f"https://huggingface.co/{repository}/resolve/{revision}/{relative_path}"
```

Stream into a temporary file while hashing; check byte count and SHA-256 before `os.replace`.
Existing valid files are reused only after rehashing. Write `model-manifest.json` with repository,
revision, exact files, direct dependency versions from `importlib.metadata`, Python version, and
provider. Never import this module from the evaluator.

- [ ] **Step 5: Write RED ONNX boundary tests with fakes**

```python
import numpy as np

class RerankTests(unittest.TestCase):
    def test_only_unprotected_candidates_are_scored(self):
        seen = []
        def scorer(query, passages):
            seen.extend(passages)
            return [0.2, 0.9]
        got = semantic_onnx.rerank(
            "q", ["protected", "a", "b"],
            {"protected": "p", "a": "a", "b": "b"},
            {"protected"}, scorer,
        )
        self.assertEqual(seen, ["a", "b"])
        self.assertEqual([note_id for note_id, _ in got], ["protected", "b", "a"])

    def test_equal_logits_preserve_b1_order(self):
        got = semantic_onnx.rerank(
            "q", ["b", "a"], {"a": "A", "b": "B"}, set(),
            lambda query, passages: [1.0, 1.0],
        )
        self.assertEqual([note_id for note_id, _ in got], ["b", "a"])


class FakeInput:
    def __init__(self, name):
        self.name = name


class FakeSession:
    def __init__(self, input_names=("input_ids", "attention_mask")):
        self.input_names = input_names
        self.feed = None

    def get_inputs(self):
        return [FakeInput(name) for name in self.input_names]

    def run(self, _outputs, feed):
        self.feed = feed
        return [np.asarray([[0.25]], dtype="float32")]


class FakeEncoding:
    ids = [0, 10, 2, 2, 20, 2]
    attention_mask = [1, 1, 1, 1, 1, 1]
    type_ids = [0, 0, 0, 1, 1, 1]


class FakeTokenizer:
    def __init__(self):
        self.truncation = None
        self.pairs = None

    def enable_truncation(self, **kwargs):
        self.truncation = kwargs

    def enable_padding(self, **_kwargs):
        pass

    def encode_batch(self, pairs):
        self.pairs = pairs
        return [FakeEncoding() for _pair in pairs]


class RuntimeBoundaryTests(unittest.TestCase):
    def test_pair_order_truncation_and_graph_feed(self):
        tokenizer, session = FakeTokenizer(), FakeSession()
        reranker = semantic_onnx.OnnxReranker(tokenizer, session, max_length=512)
        self.assertEqual(reranker.score("query", ["passage"]), [0.25])
        self.assertEqual(tokenizer.pairs, [("query", "passage")])
        self.assertEqual(tokenizer.truncation, {"max_length": 512, "strategy": "only_second"})
        self.assertEqual(set(session.feed), {"input_ids", "attention_mask"})
        self.assertEqual(session.feed["input_ids"].dtype, np.int64)

    def test_optional_token_type_ids_use_encoding_values(self):
        session = FakeSession(("input_ids", "attention_mask", "token_type_ids"))
        reranker = semantic_onnx.OnnxReranker(FakeTokenizer(), session, max_length=512)
        reranker.score("query", ["passage"])
        self.assertEqual(session.feed["token_type_ids"].tolist(), [[0, 0, 0, 1, 1, 1]])

    def test_unknown_required_graph_input_fails_closed(self):
        reranker = semantic_onnx.OnnxReranker(
            FakeTokenizer(), FakeSession(("input_ids", "mystery_input")), max_length=512,
        )
        with self.assertRaisesRegex(semantic_onnx.OnnxSetupError, "mystery_input"):
            reranker.score("query", ["passage"])
```

These tests pin optional `token_type_ids` from the encoding, pair order, `only_second` truncation,
int64 inputs, accepted graph names, and fail-closed behavior for an unknown required input.

- [ ] **Step 6: Implement lazy ONNX inference**

`semantic_onnx.py` imports numpy, tokenizers, and onnxruntime only inside `OnnxReranker.load` or
`score`. Configure:

```python
options = ort.SessionOptions()
options.intra_op_num_threads = 1
options.inter_op_num_threads = 1
options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
session = ort.InferenceSession(str(model_path), sess_options=options, providers=["CPUExecutionProvider"])
```

Define `OnnxSetupError(RuntimeError)` in `semantic_onnx.py`. Use `Tokenizer.from_file`; in
`OnnxReranker.__init__`, call `enable_truncation(max_length=max_length,
strategy="only_second")` and padding with pad ID 1. `encode_batch([(query, passage), ...])`,
dynamically pad each batch, and feed int64 arrays for `input_ids`, `attention_mask`, and optional
`token_type_ids`. Flatten the single output logit to one Python float per pair; a different output
cardinality raises `OnnxSetupError`.

- [ ] **Step 7: Run model-boundary tests without real model**

```bash
python scripts/test_semantic_onnx.py
```

Expected: fetch/hash and fake runtime tests pass; the real-model smoke is skipped with an explicit
artifact/runtime-unavailable reason.

- [ ] **Step 8: Commit the optional model boundary**

```bash
git add requirements-semantic-baseline.txt scripts/fetch_reranker.py scripts/semantic_onnx.py scripts/test_semantic_onnx.py scripts/fixtures/vector-semantics/baseline-v1/config.json
git commit -m "feat(recall): add pinned zero-shot reranker boundary"
```

---

### Task 6: Orchestrate train selection, dev gate, B2, and artifact checks

**Files:**
- Modify: `scripts/semantic_baseline.py`
- Create: `scripts/semantic_baseline_core.py` (extract the already-green pure functions before adding orchestration)
- Modify: `scripts/test_semantic_baseline.py`
- Modify: `scripts/test_semantic_onnx.py`

**Interfaces:**
- Consumes: Tasks 2–5 APIs and baseline config.
- Produces:
  - `preflight_inputs(config_path: Path, stage: str, model_manifest: Path | None = None) -> dict`
  - `load_baseline_split(root: Path, split: str, loader=corpus_lint.load_split) -> dict`
  - `validate_query_coverage(expected_ids: list[str], results: dict[str, dict]) -> None`
  - `assert_repeatable_rankings(runs: list[dict[str, list[str]]]) -> str`
  - `require_new_output(path: Path) -> None`
  - `environment_metadata(package_names: tuple[str, ...], provider: str | None, *, package_version=importlib.metadata.version, git_commit=current_git_commit, platform_info=platform.platform, cpu_name=platform.processor) -> dict`
  - `run_train(config_path: Path, out: Path, *, preflight_fn=preflight_inputs, load_split=corpus_lint.load_split, evaluate_policies=evaluate_all_candidate_policies) -> dict`
  - `finish_dev(candidate_report: dict, reranker_factory: Callable) -> dict`
  - `run_dev(config_path: Path, out: Path, model_manifest: Path, *, preflight_fn=preflight_inputs, load_split=corpus_lint.load_split, reranker_factory=semantic_onnx.OnnxReranker.load) -> dict`
  - CLI `semantic_baseline.py preflight --config PATH --model-manifest PATH`
  - CLI `semantic_baseline.py train --config PATH --out DIR`
  - CLI `semantic_baseline.py dev --config PATH --out DIR --model-manifest PATH`
  - CLI `semantic_baseline.py check --config PATH --out DIR`
  - CLI `semantic_baseline.py render --out DIR --evidence PATH`
  - exit `0` success, `2` `SETUP_ERROR`, `3` `CANDIDATE_NO_GO`.

- [ ] **Step 1: Write RED state-machine tests**

Use a temporary output directory, the committed config, and explicit injected collaborators:

```python
class BaselineStateTests(unittest.TestCase):
    def setUp(self):
        self.work = Path(tempfile.mkdtemp(prefix="baselinecase_", dir="/tmp"))
        self.addCleanup(lambda: shutil.rmtree(self.work, ignore_errors=True))
        source = Path(__file__).parent / "fixtures/vector-semantics/baseline-v1/config.json"
        self.config = self.work / "config.json"
        shutil.copy2(source, self.config)

    def test_train_writes_all_fourteen_policies_and_freezes_one(self):
        split = {"notes": [], "queries": []}
        reports = [policy_report(name, reserve, 1.0) for name, reserve in baseline.policy_grid()]
        result = baseline.run_train(
            self.config, self.work,
            preflight_fn=lambda *_args, **_kwargs: None,
            load_split=lambda root, split_name, certified_run=False: split,
            evaluate_policies=lambda *_: reports,
        )
        self.assertEqual(result["status"], "POLICY_SELECTED")
        report = json.loads((self.work / "train-report.json").read_text(encoding="utf-8"))
        policy = json.loads((self.work / "selected-policy.json").read_text(encoding="utf-8"))
        self.assertEqual(len(report["policies"]), 14)
        self.assertEqual(policy["config_sha256"], result["config_sha256"])

    def test_dev_rejects_policy_hash_drift_before_loading_split(self):
        (self.work / "selected-policy.json").write_bytes(baseline.canonical_json_bytes({
            "config_sha256": "0" * 64,
        }))
        with self.assertRaisesRegex(baseline.SetupError, "config_sha256"):
            baseline.run_dev(
                self.config, self.work, self.work / "model-manifest.json",
                preflight_fn=lambda *_args, **_kwargs: None,
                load_split=lambda *_args, **_kwargs: self.fail("loader called before policy check"),
            )

    def test_candidate_failure_does_not_invoke_reranker(self):
        called = []
        result = baseline.finish_dev(
            candidate_report={"gate_errors": ["safety Recall@20 5/6 != 6/6"]},
            reranker_factory=lambda *_: called.append(True),
        )
        self.assertEqual(result["status"], "CANDIDATE_NO_GO")
        self.assertEqual(called, [])

    def test_split_guard_never_opens_certification(self):
        calls = []
        def loader(_root, split_name, certified_run=False):
            calls.append((split_name, certified_run))
            return {"notes": [], "queries": []}
        baseline.load_baseline_split(Path("corpus-v2"), "train", loader=loader)
        self.assertEqual(calls, [("train", False)])
        with self.assertRaisesRegex(baseline.BaselineError, "certification"):
            baseline.load_baseline_split(Path("corpus-v2"), "certification", loader=loader)

    def test_repeatability_rejects_changed_order(self):
        runs = [{"q-1": ["n-a", "n-b"]} for _ in range(19)]
        runs.append({"q-1": ["n-b", "n-a"]})
        with self.assertRaisesRegex(baseline.SetupError, "determinism"):
            baseline.assert_repeatable_rankings(runs)

    def test_missing_query_result_is_a_setup_error(self):
        with self.assertRaisesRegex(baseline.SetupError, "q-2"):
            baseline.validate_query_coverage(["q-1", "q-2"], {"q-1": {}})

    def test_existing_dev_report_is_immutable(self):
        path = self.work / "dev-report.json"
        path.write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(baseline.SetupError, "already exists"):
            baseline.require_new_output(path)

    def test_environment_metadata_has_required_identity(self):
        got = baseline.environment_metadata(
            ("numpy",), "CPUExecutionProvider",
            package_version=lambda _name: "1.0", git_commit=lambda: "abc123",
            platform_info=lambda: "Windows-test", cpu_name=lambda: "CPU-test",
        )
        self.assertEqual(got["git_commit"], "abc123")
        self.assertEqual(got["dependencies"], {"numpy": "1.0"})
        self.assertEqual(got["provider"], "CPUExecutionProvider")
        self.assertEqual(got["cpu"], "CPU-test")
        self.assertEqual(got["platform"], "Windows-test")
        self.assertIn("python", got)
```

- [ ] **Step 2: Run state tests and verify RED**

```bash
python scripts/test_semantic_baseline.py BaselineStateTests -v
```

Expected: missing orchestration functions.

- [ ] **Step 3: Implement preflight and train command**

Implement `preflight_inputs(config_path, stage, model_manifest=None)` with stages `train` and
`dev`. Both stages verify Python 3.12, corpus version/frozen/hash, an empty
`corpus_lint.check_no_cert_reference(config)` result, and Potion SHA-256. Train requires exact
numpy/model2vec/tokenizers versions; dev additionally requires exact onnxruntime, model-manifest
identity, and `CPUExecutionProvider`.

`load_baseline_split` accepts only `train` or `dev` and always delegates with
`certified_run=False`. `validate_query_coverage` requires exact equality between expected and
result query-ID sets; duplicates are rejected while loading.

`run_train` invokes its injected preflight before loading data. It loads train once, computes both
B0 variants and Potion ranks once per query, evaluates all 14 policies, validates complete query
coverage, and writes `train-report.json` plus `selected-policy.json` atomically. The policy file
contains either the selected tokenizer/quota or `"selected": null` with gate failures. Return
`POLICY_SELECTED` or `CANDIDATE_NO_GO` accordingly.

- [ ] **Step 4: Implement immutable dev command**

`run_dev` validates the selected-policy config hash before any other artifact read, preflight, or
split load. It then calls `require_new_output(dev-report.json)`, validates the remaining
selected-policy/train-report/model identities, and calls `load_split("dev")` once. It computes both
B0 variants and the frozen B1 policy. On B1 failure it writes a complete dev report with
`CANDIDATE_NO_GO` and returns exit 3.

On B1 pass, instantiate the real reranker and score only B1 candidates. Catch
`semantic_onnx.OnnxSetupError` and map it to `SetupError` at this boundary. Repeat B2 inference
twenty times over the same in-memory query/note pairs and pass all rank maps to
`assert_repeatable_rankings`. The helper returns the aggregate ordered-ID SHA-256 only when every
query order is identical across all runs. Write `BASELINE_COMPLETE` even when B2 quality is poor,
because B2 has no gate.

- [ ] **Step 5: Implement baseline manifest and check command**

The final manifest has `baseline_version`, terminal `status`, and a `files` map. Pin its shape with
executable assertions:

```python
expected_complete_files = {
    "config.json", "dev-report.json", "model-manifest.json",
    "selected-policy.json", "train-report.json",
}
self.assertEqual(set(manifest["files"]), expected_complete_files)
self.assertTrue(all(re.fullmatch(r"[0-9a-f]{64}", value)
                    for value in manifest["files"].values()))
self.assertEqual(manifest["baseline_version"], "v1")
self.assertEqual(manifest["status"], "BASELINE_COMPLETE")
```

For train candidate NO-GO, the manifest contains config, model manifest, selected-policy
(`selected: null` plus failure evidence), and train report; it omits dev report. For dev candidate
NO-GO, dev report is mandatory. `check` derives the required path set from terminal status,
recomputes every declared hash, and validates cross-file config, corpus, policy, Potion, and model
identities without running inference.

Every train/dev report includes one `environment` object with `git_commit`, `platform`, `python`,
`cpu`, exact direct dependency versions, ONNX provider when applicable, and per-stage diagnostic
timings in milliseconds. `environment_metadata` accepts injected version/command/platform readers
in tests so a fresh clone does not need optional model packages for unit tests.

- [ ] **Step 6: Generate Markdown from report data**

Add `render_evidence(train_report: dict, dev_report: dict | None, manifest: dict) -> str`. It
renders exact counts, percentages, confidence intervals, per-language/family tables, selected
policy, B0-current vs B0-unicode delta, B1 gate evidence, B2 corrections/regressions when present,
determinism hashes, environment, and a prominent statement that Windows timings are diagnostic.
It formats values already present in JSON and does not recalculate metrics.

- [ ] **Step 7: Run complete automated verification**

```bash
python scripts/test_semantic_baseline.py
python scripts/test_semantic_onnx.py
python scripts/test_corpus_lint.py
python scripts/test_recall.py
python scripts/test_vecidx.py
python scripts/corpus_build.py --check
```

Expected: all tests pass; corpus hash remains unchanged.

- [ ] **Step 8: Commit orchestration**

```bash
git add scripts/semantic_baseline.py scripts/test_semantic_baseline.py scripts/test_semantic_onnx.py
git commit -m "feat(recall): orchestrate frozen train-dev baseline"
```

---

### Task 7: Run the real baseline and record evidence

**Files:**
- Generate: `scripts/fixtures/vector-semantics/baseline-v1/model-manifest.json`
- Generate: `scripts/fixtures/vector-semantics/baseline-v1/selected-policy.json`
- Generate: `scripts/fixtures/vector-semantics/baseline-v1/train-report.json`
- Generate: `scripts/fixtures/vector-semantics/baseline-v1/dev-report.json`
- Generate: `scripts/fixtures/vector-semantics/baseline-v1/manifest.json`
- Generate: `docs/wayfinder/mnemos-semantic-recall-release-decision/evidence/semantic-baseline-v1.md`
- Modify: `docs/wayfinder/mnemos-semantic-recall-release-decision/tickets/establish-production-candidate-recall.md`
- Modify: `docs/wayfinder/mnemos-semantic-recall-release-decision/MAP.md`

**Interfaces:**
- Consumes: committed evaluator/config plus public pinned model artifacts.
- Produces: immutable measured baseline evidence and a resolved candidate-recall decision.

- [ ] **Step 1: Create the isolated optional environment**

On this Windows workstation:

```bash
python -m venv .cache/semantic-baseline-venv
.cache/semantic-baseline-venv/Scripts/python.exe -m pip install -r requirements-semantic-baseline.txt
```

Expected: the four direct package versions match config. Record the complete installed package map
in `model-manifest.json`; do not add the virtual environment to git.

The Potion model is intentionally gitignored and therefore absent from a fresh worktree. Materialize
it with the pinned baseline environment before preflight:

```bash
.cache/semantic-baseline-venv/Scripts/python.exe scripts/vectors/fetch_model.py
sha256sum scripts/vectors/potion-base-8M/config.json \
  scripts/vectors/potion-base-8M/modules.json \
  scripts/vectors/potion-base-8M/tokenizer.json \
  scripts/vectors/potion-base-8M/model.safetensors
```

Expected hashes, in command order:

```text
f68ab920d7257faf6cbb4c8da5d96cc41dbbe7842b7043d92f0c2c3d3deef942
0858e4a5e4c99ece0f93eae7660195497a2667a7cfca3dc3223b68df19097056
273ca9e28ec6990aea6206b0364443754d87e87a5dd28e94026ea9999ba3bf62
f65d0f325faadc1e121c319e2faa41170d3fa07d8c89abd48ca5358d9a223de2
```

In a local worktree an existing sibling cache may be copied instead of downloaded, but the same
four hashes are mandatory before any train/dev run.

- [ ] **Step 2: Fetch and verify the pinned reranker**

```bash
.cache/semantic-baseline-venv/Scripts/python.exe scripts/fetch_reranker.py \
  --config scripts/fixtures/vector-semantics/baseline-v1/config.json \
  --manifest scripts/fixtures/vector-semantics/baseline-v1/model-manifest.json
```

Expected: five files fetched/reused at revision `1427fd652930e4ba29e8149678df786c240d8825`;
all sizes/hashes match config; no model binary is staged by git.

- [ ] **Step 3: Run preflight**

```bash
.cache/semantic-baseline-venv/Scripts/python.exe scripts/semantic_baseline.py preflight \
  --config scripts/fixtures/vector-semantics/baseline-v1/config.json \
  --model-manifest scripts/fixtures/vector-semantics/baseline-v1/model-manifest.json
```

Expected: `preflight: PASS`; corpus frozen/hash match, config has no certification reference,
Potion/model/dependencies match.

- [ ] **Step 4: Select the candidate policy on train**

```bash
.cache/semantic-baseline-venv/Scripts/python.exe scripts/semantic_baseline.py train \
  --config scripts/fixtures/vector-semantics/baseline-v1/config.json \
  --out scripts/fixtures/vector-semantics/baseline-v1
```

Expected branch:

- exit 0 / `POLICY_SELECTED`: inspect only the emitted machine counts, then continue;
- exit 3 / `CANDIDATE_NO_GO`: skip dev and B2, emit the terminal baseline manifest, then continue
  at Step 6 for integrity verification and failure evidence.

Do not add a new tokenizer/quota after seeing train results.

- [ ] **Step 5: Run the single frozen dev evaluation**

Only after `POLICY_SELECTED`:

```bash
.cache/semantic-baseline-venv/Scripts/python.exe scripts/semantic_baseline.py dev \
  --config scripts/fixtures/vector-semantics/baseline-v1/config.json \
  --out scripts/fixtures/vector-semantics/baseline-v1 \
  --model-manifest scripts/fixtures/vector-semantics/baseline-v1/model-manifest.json
```

Expected branch:

- exit 0 / `BASELINE_COMPLETE`: B1 passes, B2 metrics and 20-repeat ranking hashes are present;
- exit 3 / `CANDIDATE_NO_GO`: B1 misses at least one hard dev gate, B2 is absent, and no policy is
  changed or rerun under baseline-v1;
- exit 2 / `SETUP_ERROR`: repair only the named environment/artifact defect, preserving config and
  policy; rerun the identical command.

- [ ] **Step 6: Verify artifact integrity and regenerate evidence**

```bash
.cache/semantic-baseline-venv/Scripts/python.exe scripts/semantic_baseline.py check \
  --config scripts/fixtures/vector-semantics/baseline-v1/config.json \
  --out scripts/fixtures/vector-semantics/baseline-v1
.cache/semantic-baseline-venv/Scripts/python.exe scripts/semantic_baseline.py render \
  --out scripts/fixtures/vector-semantics/baseline-v1 \
  --evidence docs/wayfinder/mnemos-semantic-recall-release-decision/evidence/semantic-baseline-v1.md
python scripts/corpus_build.py --check
.cache/semantic-baseline-venv/Scripts/python.exe scripts/test_semantic_baseline.py
.cache/semantic-baseline-venv/Scripts/python.exe scripts/test_semantic_onnx.py
```

Expected: baseline artifact hashes PASS; corpus still reports
`f71199596a8de64b748287211d34f26b4645c74d3e410afc11c86b527a1f8ba3`; all tests pass.

Generate `semantic-baseline-v1.md` from JSON with the evaluator's renderer. Compare every displayed
numerator/denominator and hash to the machine report; the Markdown adds interpretation only.

- [ ] **Step 7: Resolve the candidate ticket and update the map**

Set `establish-production-candidate-recall.md` to `Resolved` and link the Markdown evidence plus
baseline manifest. Record exactly one conclusion:

- B1 passed: name the selected tokenizer/quota and exact dev gate counts; state that B2 is a
  descriptive zero-shot baseline and domain adaptation remains unproven; or
- B1 failed: name every failed gate and stop the semantic route at candidate retrieval.

Add the same concise decision to `MAP.md`. Never state model GO from `BASELINE_COMPLETE`.

- [ ] **Step 8: Commit measured evidence**

```bash
git add requirements-semantic-baseline.txt \
  scripts/fixtures/vector-semantics/baseline-v1 \
  docs/wayfinder/mnemos-semantic-recall-release-decision/evidence/semantic-baseline-v1.md \
  docs/wayfinder/mnemos-semantic-recall-release-decision/tickets/establish-production-candidate-recall.md \
  docs/wayfinder/mnemos-semantic-recall-release-decision/MAP.md
git commit -m "evidence(recall): record semantic baseline-v1"
```

- [ ] **Step 9: Confirm a clean, reproducible handoff**

Run the artifact `check` once from the committed tree and report:

- commit ID;
- selected policy or candidate NO-GO;
- B0-current/B0-unicode/B1 counts;
- B2 Top-1/Recall@3/corrections/regressions when present;
- unchanged corpus hash;
- test counts;
- explicit note that Windows latency remains diagnostic.
