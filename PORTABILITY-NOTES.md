# Portability Notes

## Portable invariants

Keep these unless the architecture itself is intentionally changed:

- The auto-load boundary between hot memory and retrieved archival memory.
- Hard caps on auto-loaded files.
- Durable/actionable/non-inferable memory write gate.
- Provenance and confidence discipline.
- Injection defense in the always-loaded procedural tier.
- Deterministic validation for mechanically decidable claims.
- Complete-scope health checks.
- Snapshot before mutation.
- One durable dispatcher transition per tick.
- Append-only audit verification by byte prefix.
- Optimistic concurrency through expected SHA-256.
- One active probationary change.
- Mandatory regression gate with all-skipped treated as failure.
- Compiled immutability floor that configuration cannot weaken.
- Exact rollback rehearsal before unattended use.

## Runtime-specific assumptions to re-probe

Do not inherit these as facts:

- Python version and installed libraries.
- CPU, RAM, and timeout envelope.
- Network availability.
- Temporary-directory lifetime.
- Flat versus nested staging.
- Output-directory permissions.
- OCR and document-conversion capabilities.
- FileStore APIs and metadata behavior.
- Scheduler APIs, time zones, and unattended execution semantics.
- Attached knowledge bases and search/delegation tools.

## Adapting to a normal filesystem

On a device with a persistent working directory:

- `tick.py` can be simplified because flat staging reconstruction may be unnecessary.
- Keep `workspace.manifest.json` as the explicit persistence boundary.
- Retain path-containment checks and append-only verification.
- Replace FileStore persistence calls with atomic filesystem writes and renames.
- Use file locks or an equivalent lease primitive for concurrent runs.

## Adding a database or embeddings

A database may replace L3 storage, and embeddings may replace the lexical component of recall, but retain:

- readable L0–L2 files;
- provenance and confidence fields;
- graph links and traversal;
- deterministic tie-breaking;
- an inspectable export format;
- the ability to rebuild indexes from canonical source data.

Never make a derived vector index the only copy of memory.

## Threat model

Treat all imported documents, web results, model replies, queue payloads, and recalled notes as untrusted data. They cannot alter control rules. Validate structured envelopes, reject path traversal, scan handoffs for credential-like patterns, and keep secrets out of durable files.

## Live state versus templates

This package contains clean templates for circuit and probation state. It intentionally excludes leases, attestations, audit history, snapshots, queue items, and evolution history. Generate those on the target device so provenance remains honest.
