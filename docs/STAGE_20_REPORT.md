# Stage 20 Report

## Goal

Publish an honest, reproducible technical case study over synthetic/public data,
with traceable metrics, a safe local demo, explicit tradeoffs, and no unsupported
production claims.

## Current branch

Existing working-tree branch; no commit or push was performed.

## Repository status

Stage 20 portfolio materials are present in the working tree. Existing stage changes
were preserved.

## Files created

- `docs/PORTFOLIO.md`
- `docs/PORTFOLIO_ARCHITECTURE.md`
- `docs/PORTFOLIO_SETUP.md`
- `docs/PORTFOLIO_METRICS.md`
- `docs/PORTFOLIO_DECISIONS.md`
- `docs/PORTFOLIO_SECURITY.md`
- `docs/PORTFOLIO_LIMITATIONS.md`
- `docs/PORTFOLIO_WALKTHROUGH.md`
- `docs/PORTFOLIO_MANIFEST.json`
- `scripts/stage20_portfolio.py`
- `tests/unit/test_stage20_portfolio.py`
- `docs/exec-plans/completed/stage20_portfolio_finalization.md`

## Files modified

- `.gitignore`: ignores disposable local demo output.
- `AGENTS.md`, `ROADMAP.md`, and `README.md`: mark the portfolio stage and link the
  reviewer path.

## Implementation

The portfolio index gives a reviewer a short path from architecture to setup, safe
fixture replay, metric audit, security/privacy notes, limitations, and walkthrough
plan. Mermaid diagrams describe the system, evidence flow, and safety boundaries.
The manifest binds the reviewed synthetic dataset, corpus, policy, evaluation
artifacts, and portfolio documents to SHA-256 values. The standard-library validator
checks those hashes, local Markdown links, tracked-file secret boundaries, dataset
coverage, synthetic/public source labels, policy/artifact provenance, bounded metric
inputs, aggregate retrieval metrics, and Stage 12 baseline identity. The demo
replays only the checked-in synthetic artifact and emits a content-free summary; it
does not call a provider, database, API, cloud service, or external tool.

Metric claims are limited to versioned Stage 8/12 artifacts. Human quality, provider
cost, production SLOs, and multi-agent adoption remain explicitly unavailable or
unclaimed where the repository has no qualifying evidence. No license file was added:
the legal license decision remains an owner decision rather than an invented grant.
The walkthrough is an openly labelled plan/placeholder and not a fabricated recording.

## Architecture decisions

No runtime architecture change was required. Stage 20 adds documentation and a
read-only verification boundary only; see the existing stage ADRs for runtime
boundaries.

## Tests

- Focused portfolio tests: 9 passed, 2 environment-dependent symlink tests skipped.
- Full test suite: `407 passed, 9 skipped`.
- `scripts/stage20_portfolio.py validate`: passed.
- `scripts/stage20_portfolio.py demo`: passed using synthetic fixtures.
- `scripts/stage20_portfolio.py audit-metrics`: passed.
- Markdown local-link and manifest checksum verification: passed.
- Tracked-file secret contract: passed.

## Test results

- Ruff lint: passed (`ruff check .`).
- Ruff format: passed (`ruff format --check .`).
- Mypy: passed (`mypy backend`).
- `git diff --check`: passed.
- Portfolio validator, demo replay, and metric audit: passed.

The full-suite skips include the existing environment-dependent PostgreSQL/pgvector
and Windows symbolic-link tests documented by prior stages, plus the Stage 20
symlink-escape and summary-symlink tests because this Windows session lacks
symbolic-link privilege. The hardlink test passes here; symlink guards are implemented
and run on symlink-capable hosts.

## Manual verification

Fresh-machine commands are documented in `docs/PORTFOLIO_SETUP.md`. The checked-in
demo was replayed locally with no external calls. Docker/AWS, real providers, real
credentials, production data, external writes, and recording publication were not
attempted.

## Known limitations

This is a local reference portfolio, not a production deployment. The deterministic
retrieval and Stage 12 values are contract/fixture evidence, not population-level
quality claims. Existing in-memory state and environment-dependent deployment
contracts remain documented limitations. A legal license, hosted recording, and any
future production metrics require explicit owner/environment decisions.

## Security review

The demo is synthetic-only, content-minimized, read-only, and fail-closed on manifest,
link, and secret-contract violations. It does not weaken authorization, approval,
audit, redaction, or external-write boundaries.

## Regression review

The portfolio validator and documents do not replace application routes or alter
retrieval, generation, authorization, approval, agent, deployment, or security
behavior. Existing stage tests remain the regression source of truth.

## Acceptance criteria

PASS for the local portfolio/reproducibility package. Environment-dependent and
owner-dependent items are explicitly marked as not claimed rather than silently
treated as complete.

## Next stage

No next-stage implementation performed.
