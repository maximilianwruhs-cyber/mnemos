# Evidence — Semantic Baseline v1

**Status:** `CANDIDATE_NO_GO`  
**Selected policy:** `current_ascii-16-4`

## Train lexical references

- B0-current Recall@20: **70/84**
- B0-unicode Recall@20: **70/84**

## Dev candidate gate

- B1 Recall@20: **148/156**
- Safety Recall@20: **100/108**
- Identifier Recall@20: **12/12**

### B1 By language

| Slice | Top-1 | Recall@3 | Recall@20 |
|---|---:|---:|---:|
| de | 36/78 | 50/78 | 71/78 |
| en | 42/78 | 63/78 | 77/78 |

### B1 By contrast family

| Slice | Top-1 | Recall@3 | Recall@20 |
|---|---:|---:|---:|
| apply-vs-rollback | 11/18 | 15/18 | 17/18 |
| cause-vs-coincidence | 6/12 | 9/12 | 11/12 |
| current-vs-superseded | 8/18 | 13/18 | 17/18 |
| identifier-tokens | 12/12 | 12/12 | 12/12 |
| mutate-vs-inspect | 6/18 | 12/18 | 17/18 |
| online-vs-offline | 8/12 | 9/12 | 12/12 |
| ordinary-paraphrase | 10/36 | 22/36 | 36/36 |
| permit-vs-prohibit | 13/18 | 14/18 | 17/18 |
| success-vs-failure | 4/12 | 7/12 | 9/12 |

### B1 Missed Queries

- `q-canary-promote-de-0`: Candidates searched: ['n-session-timeout-hard0-de', 'n-offline-export-gold-de', 'n-release-tag-hard0-de', 'n-flag-rollout-state-gold-de', 'n-dns-ttl-gold-de', 'n-dashboard-refresh-gold-de', 'n-session-timeout-gold-de', 'n-import-job-outcome-gold-de', 'n-webhook-retry-hard0-de', 'n-dns-cutover-hard0-de', 'n-dns-ttl-hard0-de', 'n-thumbnail-pipeline-hard0-de', 'n-canary-promote-hard0-de', 'n-cert-rotation-apply-hard0-de', 'n-ledger-readonly-gold-de', 'n-dns-cutover-gold-de', 'n-gc-pause-cause-hard0-de', 'n-rate-limit-gold-de', 'n-thumbnail-pipeline-gold-de', 'n-shard-rebalance-gold-de']
- `q-import-job-outcome-de-0`: Candidates searched: ['n-offline-otp-gold-de', 'n-queue-backlog-cause-hard0-de', 'n-release-tag-gold-de', 'n-sdk-deprecation-hard0-de', 'n-session-timeout-hard0-de', 'n-offline-otp-hard0-de', 'n-ledger-readonly-hard0-de', 'n-flag-rollout-state-hard0-de', 'n-dashboard-refresh-gold-de', 'n-gc-pause-cause-gold-de', 'n-shard-rebalance-gold-de', 'n-dns-cutover-gold-de', 'n-release-tag-hard0-de', 'n-import-job-outcome-hard0-de', 'n-session-timeout-gold-de', 'n-offline-export-gold-de', 'n-shard-rebalance-hard0-de', 'n-vpn-access-hard0-de', 'n-runbook-version-gold-de', 'n-rate-limit-gold-de']
- `q-import-job-outcome-de-1`: Candidates searched: ['n-queue-backlog-cause-hard0-de', 'n-import-job-outcome-hard0-de', 'n-queue-peek-consume-hard0-de', 'n-session-timeout-hard0-de', 'n-offline-export-gold-de', 'n-rate-limit-hard0-de', 'n-temp-admin-grant-hard0-de', 'n-rate-limit-gold-de', 'n-dashboard-refresh-hard0-de', 'n-cron-window-gold-de', 'n-vpn-access-hard0-de', 'n-dashboard-refresh-gold-de', 'n-gc-pause-cause-gold-de', 'n-bucket-public-gold-de', 'n-jobid-jr7788-hard0-de', 'n-webhook-retry-hard0-de', 'n-offline-otp-gold-de', 'n-session-timeout-gold-de', 'n-flag-rollout-state-gold-de', 'n-flag-rollout-state-hard0-de']
- `q-queue-backlog-cause-de-2`: Candidates searched: ['n-thumbnail-pipeline-hard0-de', 'n-webhook-retry-hard0-de', 'n-queue-peek-consume-hard0-de', 'n-sdk-deprecation-hard0-de', 'n-queue-backlog-cause-hard0-de', 'n-import-job-outcome-hard0-de', 'n-rate-limit-hard0-de', 'n-dashboard-refresh-gold-de', 'n-gc-pause-cause-gold-de', 'n-dns-ttl-hard0-de', 'n-temp-admin-grant-gold-de', 'n-thumbnail-pipeline-gold-de', 'n-shard-rebalance-gold-de', 'n-gc-pause-cause-hard0-de', 'n-dashboard-refresh-hard0-de', 'n-webhook-retry-gold-de', 'n-queue-peek-consume-gold-de', 'n-temp-admin-grant-hard0-de', 'n-runbook-version-hard0-de', 'n-dns-cutover-gold-de']
- `q-sdk-deprecation-de-2`: Candidates searched: ['n-session-timeout-hard0-de', 'n-gc-pause-cause-gold-de', 'n-shard-rebalance-hard0-de', 'n-runbook-version-gold-de', 'n-rate-limit-hard0-de', 'n-thumbnail-pipeline-hard0-de', 'n-sdk-deprecation-hard0-de', 'n-queue-backlog-cause-hard0-de', 'n-webhook-retry-hard0-de', 'n-release-tag-gold-de', 'n-gc-pause-cause-hard0-de', 'n-cert-rotation-apply-hard0-de', 'n-dashboard-refresh-gold-de', 'n-queue-peek-consume-gold-de', 'n-queue-peek-consume-hard0-de', 'n-runbook-version-hard0-de', 'n-dns-ttl-gold-de', 'n-session-timeout-gold-de', 'n-release-tag-hard0-de', 'n-offline-export-gold-de']
- `q-shard-rebalance-en-2`: Candidates searched: ['n-cert-rotation-apply-gold-en', 'n-import-job-outcome-hard0-en', 'n-session-timeout-gold-en', 'n-shard-rebalance-hard0-en', 'n-offline-export-gold-en', 'n-dns-cutover-gold-en', 'n-dns-cutover-hard0-en', 'n-ledger-readonly-hard0-en', 'n-ledger-readonly-gold-en', 'n-gc-pause-cause-hard0-en', 'n-dashboard-refresh-gold-en', 'n-temp-admin-grant-hard0-en', 'n-cert-rotation-apply-hard0-en', 'n-canary-promote-hard0-en', 'n-import-job-outcome-gold-en', 'n-release-tag-gold-en', 'n-rate-limit-gold-en', 'n-offline-otp-gold-en', 'n-queue-backlog-cause-hard0-en', 'n-rate-limit-hard0-en']
- `q-vpn-access-de-2`: Candidates searched: ['n-vpn-access-hard0-de', 'n-offline-export-hard0-de', 'n-dashboard-refresh-hard0-de', 'n-cron-window-hard0-de', 'n-temp-admin-grant-gold-de', 'n-bucket-public-hard0-en', 'n-temp-admin-grant-hard0-de', 'n-vpn-access-hard0-en', 'n-jobid-jr7788-hard0-de', 'n-shard-rebalance-hard0-de', 'n-dns-ttl-hard0-de', 'n-ledger-readonly-hard0-de', 'n-offline-otp-hard0-de', 'n-ledger-readonly-gold-de', 'n-shard-rebalance-gold-de', 'n-cron-window-gold-de', 'n-session-timeout-hard0-de', 'n-rate-limit-gold-de', 'n-dns-ttl-gold-de', 'n-session-timeout-gold-de']
- `q-webhook-retry-de-1`: Candidates searched: ['n-rate-limit-hard0-de', 'n-rate-limit-gold-de', 'n-queue-backlog-cause-hard0-de', 'n-session-timeout-hard0-de', 'n-release-tag-hard0-de', 'n-dashboard-refresh-hard0-de', 'n-bucket-public-gold-de', 'n-cron-window-hard0-de', 'n-dashboard-refresh-gold-de', 'n-runbook-version-hard0-de', 'n-session-timeout-gold-de', 'n-gc-pause-cause-hard0-de', 'n-sdk-deprecation-hard0-de', 'n-thumbnail-pipeline-gold-de', 'n-cron-window-gold-de', 'n-flag-rollout-state-hard0-de', 'n-gc-pause-cause-gold-de', 'n-webhook-retry-hard0-de', 'n-offline-export-hard0-de', 'n-vpn-access-gold-de']

B2 was not run because candidate retrieval did not clear its gate.

## File Identity & Provenance

- Corpus hash: `f71199596a8de64b748287211d34f26b4645c74d3e410afc11c86b527a1f8ba3`
- `config.json`: `20434c07760b54b481f2ae3a0e37f914544e9405b6c38d480b3cb36a8ab7f219`
- `dev-report.json`: `fa5030e892d6162fcfdd644387c4df8337c39b24a3517d804d7316d1f00d64df`
- `model-manifest.json`: `7013165ccde66df46120c2633fba9d1cf046eec5b791105a8c0a6dc75eaef6e6`
- `selected-policy.json`: `414ac9056f455b8d8471987f3acba426da5fdce9a4cd16e027e79b6f1a159486`
- `train-report.json`: `2fb9b8c0c6a5782c6e9a02583f137e97b9957549b9ca443e3618368c45c19146`

## Environment

- Python: `3.12.10`
- Platform: `Windows-11-10.0.26200-SP0`
- Provider: `CPUExecutionProvider`
- Dependencies: `model2vec==0.9.0`, `numpy==2.1.3`, `onnxruntime==1.20.1`, `tokenizers==0.21.0`
