# Exclusions and Sanitization Record

The following source-workspace material is intentionally excluded from this portable kit:

| Excluded material | Reason |
|---|---|
| Live `USER.md` | Operator-specific personal context |
| Live `MEMORY.md` and `Memory/{context,lessons,decisions,preferences,daily}` | Instance-specific learned state and history |
| `Memory/_archive/**` | Historical backups, not implementation source |
| Live autonomy state | Device/run-specific circuit, probation, lease, fingerprints, and attestations |
| Live audit JSONL | Instance-specific operational history |
| Queue items, artifacts, snapshots, evolution proposals | Runtime work products |
| `/uploads/**` except the explicitly included historical blueprint | Source documents may contain unrelated/private material |
| `/bots/**` | Separate completed workstream, not required by MNEMOS |
| `/code-interpreter-output/**` | Generated artifacts and debris |
| Schedule definitions and IDs | Platform-, user-, and timezone-specific; require explicit approval |
| Credentials and secrets | Must never be packaged |

Clean templates replace root context and initial autonomy state. Documentation may mention the original implementation’s operator and environment because it records architecture provenance; it is not installed as active operator state.
