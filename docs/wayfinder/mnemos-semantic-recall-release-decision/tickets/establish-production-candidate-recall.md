# Establish production candidate recall

- **Status:** Open
- **Type:** Prototype
- **Mode:** HITL
- **Assignee:** Unassigned
- **Blocked by:** [Build the frozen evidence corpus](build-the-frozen-evidence-corpus.md)

## Question

On the full evidence note pool, what bounded union of deterministic BM25/graph/recency/salience evidence and Potion similarity produces at most 20 candidates while meeting >=99% overall gold Recall@20, 100% safety-slice recall, and exact-token protection? If no policy passes, what candidate-stage limitation causes the semantic route to stop?
