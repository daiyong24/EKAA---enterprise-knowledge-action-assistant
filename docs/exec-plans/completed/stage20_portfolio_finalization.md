# Stage 20 Execution Plan — Portfolio Finalization

## Scope

Create an honest, reproducible technical case study over synthetic/public data.

## Steps and acceptance checks

1. Read architecture, business rules, roadmap, and prior stage reports.
2. Create a portfolio index, architecture diagrams, setup guide, metric source audit,
   decision log, security/privacy notes, limitations, and walkthrough placeholder.
3. Bind reviewed inputs and published evidence to a SHA-256 manifest.
4. Implement a standard-library-only validator for required files, checksums, links,
   safe tracked-file secret boundaries, fixture coverage, and metric aggregates.
5. Implement a safe local demo replay which writes only a content-free summary.
6. Add focused tests for validation, replay, and unsafe output handling.
7. Run full tests, lint, formatting, type checks, diff checks, and demo commands.
8. Record exact results and limitations in `docs/STAGE_20_REPORT.md`.

## Non-goals

No confidential data, invented benchmarks, real external calls, AWS deployment,
production SLO/security claim, frontend, chat memory, unattended write, or fabricated
recording.

## Exit condition

Reviewer can reproduce the local demo and trace every published metric to a versioned
run/artifact; unsupported values are labelled unavailable; no next-stage implementation
is performed.
