# Decide the offline companion package

- **Status:** Resolved
- **Type:** Grilling
- **Mode:** HITL
- **Assignee:** inline authoring (subagent env unavailable)
- **Blocked by:** [Confirm model and runtime redistribution viability](confirm-model-and-runtime-redistribution-viability.md); [Prove domain adaptation viability](prove-domain-adaptation-viability.md)

## Question

How should the certified Linux companion package model/tokenizer artifacts, offline wheels, native libraries, license notices, hashes, installation receipt, and clean-environment verification while remaining no larger than 150 MB installed and leaving the MNEMOS core stdlib-only?

## Findings

Evidence: [`evidence/certified-offline-companion-package-report-v1.md`](../evidence/certified-offline-companion-package-report-v1.md)

**Answer — No companion package or external dependencies will be shipped. Status: TERMINAL_NO_GO_STANDALONE.**

- **Resolution:** Due to the terminal model-viability failure, distributing a companion package is obsolete. No binary wheels, tokenizer configurations, ONNX libraries, or native dependencies will be packaged.
- **Benefits:**
  - **Prisinte Core preserved:** MNEMOS core remains 100% standard-library-only.
  - **Zero-footprint bloat:** 0.0 MB added to the installed product.
  - **Zero licensing risks:** No third-party commercial redistribution notices required.

Signed by: `[SIGNATURE: MNEMOS-RELEASE-WAYFINDER-AGENT-20260904-COMPANION-PACKAGE]`
