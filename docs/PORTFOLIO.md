# EKAA Portfolio Case Study

EKAA is an internal engineering and IT knowledge-assistant reference system. It
answers from authorized evidence and proposes (but does not autonomously execute)
consequential actions. This portfolio package is deliberately reproducible and
honest: demo inputs are synthetic, provider calls are not required, and every
published number points to a versioned artifact or a test run.

## Reviewer path

1. Read [the architecture](PORTFOLIO_ARCHITECTURE.md) and [the decision log](PORTFOLIO_DECISIONS.md).
2. Prepare Python 3.12 and install the project using the [setup guide](PORTFOLIO_SETUP.md).
3. Run `python scripts/stage20_portfolio.py validate`.
4. Run `python scripts/stage20_portfolio.py demo --output-dir .stage20-demo-output`.
5. Run `python scripts/stage20_portfolio.py audit-metrics` to verify metric provenance.
6. Read [the measured results](PORTFOLIO_METRICS.md), [security/privacy notes](PORTFOLIO_SECURITY.md), and [limitations](PORTFOLIO_LIMITATIONS.md).
7. Use [the recorded walkthrough plan](PORTFOLIO_WALKTHROUGH.md). No recording is
   included or implied by this repository.

The one-command validation also checks internal Markdown links, manifest checksums,
the synthetic-only data boundary, and the checked-in demo contract. It never calls
an external provider or service.

## Accessibility and document review

The package uses plain Markdown headings, tables, descriptive link labels, and text
fallbacks for every Mermaid diagram (the diagram source remains readable when a
renderer is unavailable). The reviewer path is keyboard/terminal friendly and does
not depend on color, audio, or a visual-only dashboard. Before publishing a rendered
copy, review heading order, contrast, code-block readability, and alternative text
for any added media.

## What is implemented

The repository contains a staged FastAPI/RAG reference implementation, reviewed
synthetic evaluation data, scoped authorization and approval boundaries, a bounded
single-agent baseline, a non-default multi-agent candidate, observability/security
controls, and local Docker/CI release contracts. The application remains a reference
implementation rather than a production deployment.

## Evidence and claims policy

The portfolio does not invent a benchmark. Retrieval numbers come from
`docs/evaluation/artifacts/synthetic_retrieval_baseline_v1.json`; the single-agent
contract result comes from `docs/evaluation/stage12_baseline_v1.json`. Human quality
scores are unavailable until a source-bound calibrated assessment is supplied, and
multi-agent adoption is explicitly not claimed. Local test counts are regenerated
by the validation command and should not be treated as performance benchmarks.

## Repository map

| Area | Entry point |
| --- | --- |
| Architecture | `docs/PORTFOLIO_ARCHITECTURE.md` |
| Local setup and replay | `docs/PORTFOLIO_SETUP.md` |
| Metric provenance | `docs/PORTFOLIO_METRICS.md` |
| Decisions and tradeoffs | `docs/PORTFOLIO_DECISIONS.md` |
| Security/privacy | `docs/PORTFOLIO_SECURITY.md` |
| Limitations | `docs/PORTFOLIO_LIMITATIONS.md` |
| Walkthrough plan | `docs/PORTFOLIO_WALKTHROUGH.md` |
| Reproducibility manifest | `docs/PORTFOLIO_MANIFEST.json` |
| Stage report | `docs/STAGE_20_REPORT.md` |
