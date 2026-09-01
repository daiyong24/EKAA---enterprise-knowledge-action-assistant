# ADR-008: Local Synthetic Reviewer Packet

## Status

Accepted — 2026-08-26

## Context

Stage 8 source artifacts deliberately exclude questions, answers, excerpts, and
provider content. That protects evaluation artifacts but means a human cannot inspect
the exact generated output that a later v2 assessment must bind to. The user has
explicitly selected one independent reviewer (`reviewer_a`) and requested a local
temporary packet that remains available for later review.

## Decision

Add an explicit opt-in reviewer-packet output only to a fresh, full-dataset,
synthetic-only local Ollama generation replay. The packet is a local Markdown file,
outside the repository, containing the question, safe displayed answer, retrieved
synthetic evidence, and server-validated claim/citation material from that same replay.
It records the raw-byte SHA-256 and run identity of the newly written unscored source
artifact. It is review material, never an evaluation artifact or scoring input.

The official JSON/CSV artifacts remain content-free. The packet is never written to
the repository, Git, logs, API responses, or a provider. The CLI requires explicit
local opt-in, the canonical reviewed synthetic corpus, a full dataset (not smoke), a
bounded reviewer ID, and a packet path outside the workspace. The operator controls
retention and deletion; no automatic deletion is performed. This decision permits
synthetic content only and must be reconsidered before any non-synthetic data.

One reviewer can create provisional human measurements after applying the documented
rubric. A solo review is not represented as independent inter-reviewer agreement and
does not promote the model by itself.

## Consequences

- Reviewers can inspect the same generated content whose source artifact will later
  bind their assessment.
- Local disk temporarily contains synthetic answer/evidence material, so the path and
  retention are explicit operator responsibilities.
- A new replay is required; existing content-free source artifacts cannot be turned
  into review packets.
- No retrieval, RAG serving, public API, provider boundary, database, or authorization
  behavior changes.
