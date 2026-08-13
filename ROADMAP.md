# EKAA Delivery Roadmap

## Roadmap Rules

The active stage is **Stage 7 — Reranking**. A stage may start only after
the prior stage passes its acceptance criteria and the user explicitly authorizes
the next stage. Security, privacy, testability, safe errors, and telemetry hooks are
cross-cutting requirements; their dedicated stages deepen and verify them.

Every stage ends with tests, review, fixes, documentation, a report, and a stop.
“Sources” in Stage 4 are retrieval-derived minimum traceability; Stage 5 adds
claim-level citation UX and validation. Synthetic/public data is mandatory until
Stage 9 is complete.

## Stage 0 — Project Foundation

- **Objective:** Establish trustworthy repository and backend foundations.
- **Deliverables:** Source-of-truth docs; Python packaging; FastAPI app; settings;
  logging; safe exception mapping; `GET /health`; pytest, Ruff, mypy, pre-commit,
  and basic GitHub Actions CI.
- **Tests:** Settings, health API contract, and public application-error contract;
  lint, formatting, and strict type checks.
- **Acceptance:** App starts; `/health` returns `200 {"status":"ok"}`; all local
  checks pass; CI definition covers the same checks; Stage 0 report is complete.
- **Non-goals:** Database, Docker, LLM, ingestion, RAG, frontend, auth, or agents.

## Stage 1 — LLM Foundation

- **Objective:** Add a provider-neutral, testable LLM application boundary.
- **Deliverables:** `POST /api/chat`; provider protocol; one implementation; prompt
  builder; structured-output contract; token usage; bounded timeout/retry and safe
  provider errors; configuration with no hard-coded secret or model.
- **Tests:** Mocked success, invalid input/output, timeout, retryable/non-retryable
  errors, retry bounds, usage mapping, and secret-safe logs.
- **Acceptance:** Stable API contract, no paid calls in tests, deterministic mocks,
  and documented provider choice/cost/privacy assumptions.
- **Non-goals:** Retrieval, enterprise factual answering, tools, or chat memory.

## Stage 2 — Document Ingestion

- **Objective:** Reliably ingest PDF, TXT, and Markdown into normalized chunks.
- **Deliverables:** Validated upload; parser/cleaner/chunker pipeline; PostgreSQL
  document and chunk metadata; Alembic migrations; checksum/idempotency; job status.
- **Tests:** Valid formats plus empty, corrupt, duplicate, oversized, unsupported,
  traversal-like names, parsing failure, transaction rollback, and chunk invariants.
- **Acceptance:** Repeatable ingestion with traceable metadata and no embeddings;
  failures are safe and leave consistent state.
- **Non-goals:** OCR, embeddings, retrieval, real confidential data, or broad format
  coverage.

## Stage 3 — Embeddings + Vector Retrieval

- **Objective:** Add measurable semantic retrieval over authorized demo content.
- **Deliverables:** Embedding protocol and one provider; pgvector migration; batch
  embedding; dimension/model metadata; top-K search and scores.
- **Tests:** Mock embeddings, dimension mismatch, partial failure, deterministic
  ranking fixture, database integration, and permission-filter-ready query shape.
- **Acceptance:** Relevant synthetic chunks are retrieved reproducibly; model
  migration/re-embedding strategy is documented.
- **Non-goals:** Generation, hybrid search, reranking, or protected content.

## Stage 4 — Basic RAG

- **Objective:** Produce evidence-bounded answers from semantic retrieval.
- **Deliverables:** Query embedding, retrieval, context/prompt builders, generation,
  insufficient-evidence behavior, and minimum retrieval-derived source references.
- **Tests:** Grounded fixture, no-evidence refusal, context/token limits, source-ID
  integrity, injection-shaped content, and provider failures.
- **Acceptance:** Answers are based only on supplied context and every source ID maps
  to an actual retrieved chunk.
- **Non-goals:** Claim-level citation UX, hybrid retrieval, or reranking.

## Stage 5 — Citation & Grounding

- **Objective:** Make individual factual claims verifiable by users.
- **Deliverables:** Claim/source citation format, excerpts, page/section metadata,
  citation validator, unsupported-claim handling, and UI-ready response contract.
- **Tests:** Citation existence, metadata accuracy, entailment-oriented fixtures,
  invalid/model-invented IDs, missing pages, and excerpt boundaries.
- **Acceptance:** All evaluated factual claims are supported or explicitly flagged;
  citation quality baseline is recorded.
- **Non-goals:** Hybrid retrieval or automated judge as sole authority.

## Stage 6 — Hybrid Retrieval

- **Objective:** Combine semantic and PostgreSQL full-text retrieval.
- **Deliverables:** Lexical index/search, score normalization or rank fusion, exact-ID
  handling, configurable candidate counts, and query diagnostics.
- **Tests:** Natural-language and identifier fixtures, duplicate merge, tie behavior,
  empty branches, filters, and database plans at representative demo scale.
- **Acceptance:** Hybrid retrieval beats or matches semantic-only on a declared
  mixed-query fixture without unacceptable latency regression.
- **Non-goals:** Reranking or production-scale tuning.

## Stage 7 — Reranking (Active)

- **Objective:** Improve final context selection with measured reranking.
- **Deliverables:** Reranker protocol/provider, candidate-to-context pipeline,
  timeout/fallback behavior, configuration, and comparison results.
- **Tests:** Ordering, score mapping, ties, failure fallback, token/size limits, and
  deterministic provider mocks.
- **Acceptance:** Measurable retrieval-quality gain justifies latency/cost; otherwise
  the feature remains disabled and the finding is documented.
- **Non-goals:** Broad RAG evaluation platform or model fine-tuning.

## Stage 8 — RAG Evaluation

- **Objective:** Establish reproducible retrieval and generation quality gates.
- **Deliverables:** Versioned golden dataset (initial target 50 reviewed questions),
  runner, Recall/Hit Rate/Precision@K, MRR, correctness, faithfulness, citation
  accuracy, unsupported-claim rate, latency, token, failure, and estimated cost.
- **Tests:** Metric formulas, dataset schema, deterministic replay, missing results,
  evaluator calibration, and regression thresholds.
- **Acceptance:** A versioned baseline with reproducible commands, confidence/caveat
  notes, and no invented results.
- **Non-goals:** Using LLM-as-judge without human calibration or claiming universal
  quality from one dataset.

## Stage 9 — Authentication & Authorization

- **Objective:** Prevent unauthorized discovery and retrieval of documents.
- **Deliverables:** Identity integration, roles/groups, document classification and
  ACLs, pre-retrieval filters, admin controls, and authorization audit events.
- **Tests:** Positive/negative matrix, cross-role leakage, enumeration, cached data,
  filters in vector/keyword paths, and fail-closed identity errors.
- **Acceptance:** No unauthorized chunk enters results, prompts, responses, logs, or
  evaluation artifacts in adversarial integration tests.
- **Non-goals:** Full enterprise IAM feature parity or multi-tenancy unless selected.

## Stage 10 — RAG Reliability

- **Objective:** Degrade safely across provider, database, and malformed-output
  failures.
- **Deliverables:** End-to-end deadlines, exponential backoff with jitter, rate-limit
  handling, circuit breakers where justified, fallback/degradation policy, health
  dependencies, and failure runbooks.
- **Tests:** Fault injection for outage, timeout, malformed JSON, low confidence,
  database failure, retry storm prevention, recovery, and cancellation.
- **Acceptance:** No single provider failure crashes the service; bounded behavior
  and user-visible degradation meet documented targets.
- **Non-goals:** Agent/tool reliability or production SLO claims without load data.

## Stage 11 — Tool Calling

- **Objective:** Introduce typed, policy-classified external tools before agents.
- **Deliverables:** Tool interface/registry, schemas, validation, read-only tools,
  proposed write tool, timeout/error contracts, and tool audit records.
- **Tests:** Selection-independent execution, schema rejection, SSRF/input threats,
  read/write classification, timeout, secret redaction, and fake integrations.
- **Acceptance:** Tools are deterministic at their boundary; no write tool can execute
  before the later approval path exists.
- **Non-goals:** Autonomous loops, LangGraph, or actual consequential writes.

## Stage 12 — Single-Agent Workflow

- **Objective:** Build and evaluate a bounded single-agent baseline with LangGraph.
- **Deliverables:** Explicit state/nodes/edges, tool selection, step/retry limits,
  checkpoints, termination rules, and read-only end-to-end workflows.
- **Tests:** Expected tool paths, no-tool answer, loop prevention, invalid state,
  recovery/checkpoint behavior, and hostile tool output.
- **Acceptance:** Baseline task suite succeeds at recorded rates within hard budgets;
  write actions remain proposals only.
- **Non-goals:** Multi-agent architecture or bypassing approval.

## Stage 13 — Human-in-the-loop

- **Objective:** Gate exact consequential actions on explicit human approval.
- **Deliverables:** Approval service/state model, parameter-bound request, expiry,
  approve/reject UI/API, authorization, idempotent execution, and audit trail.
- **Tests:** Approve/reject/expire, parameter mutation, replay, concurrent approval,
  unauthorized approver, execution failure, and crash recovery.
- **Acceptance:** Every write is impossible without valid approval and cannot execute
  more than intended; evidence is auditable end to end.
- **Non-goals:** Blanket approvals or unattended destructive actions.

## Stage 14 — Agent Evaluation

- **Objective:** Measure agent decisions and task outcomes.
- **Deliverables:** Reviewed scenarios and metrics for tool/argument accuracy, task
  success, steps, retries, unnecessary calls, escalation, approval compliance, cost,
  and latency.
- **Tests:** Scoring formulas, trace-to-result mapping, ambiguous scenarios, replay,
  evaluator agreement, and regression gates.
- **Acceptance:** Reproducible baseline exposes failure categories and prioritized
  fixes; no cherry-picked metrics.
- **Non-goals:** Multi-agent comparison without a justified candidate.

## Stage 15 — Observability / LLMOps

- **Objective:** Correlate and monitor the full AI request lifecycle safely.
- **Deliverables:** OpenTelemetry traces, Prometheus metrics, Grafana dashboards,
  correlation IDs, prompt/content redaction policy, sampling, alerts, and runbooks.
- **Tests:** Trace propagation, metric semantics/cardinality, redaction, exporter
  outage, sampling, dashboard queries, and alert drills.
- **Acceptance:** Operators can diagnose representative failures without secrets or
  protected document content leaking to telemetry.
- **Non-goals:** Treating dashboards as reliability without operational response.

## Stage 16 — Security Hardening

- **Objective:** Threat-model and adversarially validate the complete AI system.
- **Deliverables:** Threat model, prompt/tool injection controls, file and SSRF
  defenses, dependency/secret scanning, rate/abuse controls, data-flow/privacy
  review, penetration checklist, and remediation record.
- **Tests:** Injection corpus, authorization bypass, exfiltration, malicious files,
  path traversal, oversized inputs, replay, log leakage, and dependency findings.
- **Acceptance:** No unresolved critical/high finding without explicit documented
  risk acceptance; business rules remain enforceable under hostile input.
- **Non-goals:** Claiming absolute security or postponing baseline security until here.

## Stage 17 — Docker + Cloud Deployment

- **Objective:** Reproducibly run the validated system locally and on AWS.
- **Deliverables:** Minimal non-root images, Compose stack, IaC-selected AWS topology,
  dev/staging/prod separation, secrets management, TLS/network controls, migrations,
  backups, recovery procedure, and cost estimate.
- **Tests:** Image build/scan, Compose smoke test, staged deploy, migration/rollback,
  backup restore, health checks, least privilege, and configuration separation.
- **Acceptance:** Staging deploy is repeatable, observable, recoverable, and has
  documented ownership/cost; no secrets are baked into artifacts.
- **Non-goals:** Kubernetes without demonstrated need or unreviewed production data.

## Stage 18 — CI/CD Hardening

- **Objective:** Turn verified quality and release controls into enforced automation.
- **Deliverables:** Required checks, integration/evaluation/security gates, artifact
  provenance, dependency caching, staged deployment, approvals, rollback, and branch
  protection documentation.
- **Tests:** Deliberately failing gates, artifact promotion, concurrency, secret
  boundary, rollback drill, and protected-environment behavior.
- **Acceptance:** A failing required check cannot promote; deployments are traceable
  and reversible.
- **Non-goals:** Optimizing pipeline speed at the cost of assurance.

## Stage 19 — Multi-Agent Architecture

- **Objective:** Adopt multiple agents only if a measured single-agent limitation
  cannot be solved more simply.
- **Deliverables:** Written hypothesis, candidate architecture, shared-state and
  handoff rules, isolation/budgets, experiment against Stage 14 baseline, and rollback
  decision.
- **Tests:** Routing, handoff loss, loops, conflicting outputs, permissions, combined
  budget, partial failure, and comparative task suite.
- **Acceptance:** Statistically and operationally meaningful benefit exceeds added
  latency, cost, and failure surface; otherwise retain single agent.
- **Non-goals:** Multi-agent for résumé optics or unbounded delegation.

## Stage 20 — Portfolio Finalization

- **Objective:** Publish an honest, reproducible technical case study.
- **Deliverables:** Problem/architecture docs and diagrams, demo with synthetic data,
  measured RAG/agent/reliability results, decision log, setup guide, limitations,
  security/privacy notes, license, and recorded walkthrough.
- **Tests:** Fresh-machine setup, link/command verification, demo replay, metric source
  audit, secret/history scan, accessibility, and reviewer walkthrough.
- **Acceptance:** A reviewer can reproduce the demo and trace every published metric
  to a versioned run; limitations and tradeoffs are explicit.
- **Non-goals:** Invented benchmarks, confidential data, or production claims not
  supported by evidence.

## Stage Report Template

```text
Stage X Report
Goal:
Current branch:
Repository status:
Files created:
Files modified:
Implementation:
Architecture decisions:
Tests:
Test results:
Manual verification:
Known limitations:
Security review:
Regression review:
Acceptance criteria: PASS / FAIL
Next stage:
No next-stage implementation performed.
```
