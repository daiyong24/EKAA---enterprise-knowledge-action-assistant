# Enterprise Knowledge & Action Assistant (EKAA)

EKAA is a stage-built, production-style reference project for an authorized,
grounded enterprise knowledge assistant and, later, a human-approved action agent.

## Current Status

Stage 19 — Multi-Agent Architecture (Stages 9–18 complete; multi-agent candidate
measured locally and single-agent retained as default).

Implemented now: FastAPI foundation, a provider-neutral stateless LLM boundary,
safe ingestion of synthetic/public PDF, UTF-8 TXT, and UTF-8 Markdown into
normalized relational chunks, provider-neutral embeddings and PostgreSQL/pgvector
semantic retrieval, PostgreSQL full-text hybrid retrieval, bounded deterministic
reranking for RAG context selection, bounded
  retrieval-augmented generation, claim-level citation validation over that evidence,
  provider-neutral identity/authorization, reliability controls, typed policy-classified
  tools, a bounded single-agent baseline, scoped human approval, and reproducible agent
  evaluation, observability/security controls, reproducible Docker/Compose deployment,
  and gated CI/CD release contracts. LLM and RAG are disabled unless explicitly
  configured. Consequential execution remains an injected local boundary and is never
  enabled unattended. Stage 19 adds a bounded, provider-neutral specialist-route
  experiment with immutable handoffs and combined budgets; it does not replace the
  single-agent default or execute external actions.

## Requirements

- Python 3.12+
- Docker Desktop (for the optional Compose path)

## Local Setup

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
alembic upgrade head
uvicorn backend.app.main:app --reload
```

Open `http://127.0.0.1:8000/health`. Expected response:

```json
{"status":"ok"}
```

To enable a compatible provider locally, set `EKAA_LLM_ENABLED=true`,
`EKAA_LLM_API_KEY`, and `EKAA_LLM_MODEL` in your untracked `.env`. Then send a
`POST /api/chat` body such as `{"message":"Explain RAG."}`. Stage 1 accepts only
general conceptual questions and intentionally withholds free-text provider output
until grounded generation exists; tests never make provider requests.

For Stage 2, use only synthetic or explicitly public content. Set
`EKAA_DATABASE_URL` to a PostgreSQL URL in shared environments (SQLite is the local
default), apply `alembic upgrade head`, then upload with multipart form data:

```powershell
curl.exe -X POST http://127.0.0.1:8000/api/documents -F "file=@sample.txt;type=text/plain"
```

See `docs/STAGE_2_INGESTION_DESIGN.md` for MIME rules, idempotency, data handling,
and the intentionally excluded capabilities.

## Stage 17/18 Deployment and CI/CD

The supported local deployment is Docker Compose with a non-root API image and a
PostgreSQL/pgvector service. Copy `.env.docker.example` to an untracked `.env`, then
run `docker compose config` followed by `docker compose up --build -d`. The API health
check is available at `http://127.0.0.1:8000/health`. Do not put real credentials in
Compose or the repository. Staging/production use the documented AWS reference
topology (ALB + ECS Fargate + private RDS + Secrets Manager) and require a separately
reviewed migration job.

CI runs quality, PostgreSQL integration, evaluation, security, and image jobs. The
manual release workflow requires their reusable workflow to pass, an immutable
sha256 image digest, staged environment approvals, and supports explicit rollback by
digest. It does not apply AWS infrastructure in this reference repository. See
`docs/deployment/STAGE_17_DEPLOYMENT.md` and `docs/deployment/STAGE_18_CICD.md`.

## Stage 19 Multi-Agent Architecture

Stage 19 provides a deterministic candidate route (`researcher` → `verifier` →
`synthesizer`) behind an explicit experiment flag. It preserves authorization and
Stage 13 approval digests, rejects handoff loss, loops, conflicting claims, partial
failures, and combined-budget exhaustion, and can fall back to the existing
single-agent boundary. It requires the evaluation-read-only capability token and
returns evaluation-only results rather than user-facing factual answers. The
comparison harness uses complete Stage 14 replicate artifacts, an authoritative
scenario manifest to recompute suite fingerprints, provenance bindings, cost
evidence, powered statistical evidence, and positive benefit gates; without the
manifest or powered evidence, adoption is retained as single-agent. See
`docs/STAGE_19_MULTI_AGENT_DESIGN.md` and
`docs/STAGE_19_REPORT.md`.

## Stage 3 Retrieval

Stage 3's deployed path is PostgreSQL with the `pgvector` extension; SQLite remains
for isolated relational tests only and is not a vector-search implementation. Apply
the migrations against PostgreSQL, then set `EKAA_EMBEDDING_ENABLED=true`. The
included `deterministic` provider is local, reproducible, and sends no content to a
third party, but is intentionally not a quality production embedding model. A future
approved provider must be introduced through the protocol and documented data-use
review.

The batch embedding service is a controlled maintenance boundary, not a public API:
it embeds existing completed chunks in atomic batches. `POST /api/retrieval/search`
embeds a query and returns ranked, traceable synthetic/public chunks only; it never
generates an answer. See `docs/STAGE_3_EMBEDDING_RETRIEVAL_DESIGN.md`.

After provisioning a dedicated PostgreSQL database with pgvector and applying
`alembic upgrade head`, an operator may run the non-public maintenance command:

```powershell
python -m backend.app.embeddings.cli embed-pending
```

It requires `EKAA_EMBEDDING_ENABLED=true` and a PostgreSQL URL; it has no HTTP
endpoint and only processes already-completed chunks. A non-zero exit code reports
disabled configuration or a batch that needs operator retry.

## Stage 4 Basic RAG

Stage 4 adds `POST /api/rag/answer`, a separate endpoint from the conservative
Stage 1 `/api/chat` sandbox. It first embeds the question and retrieves bounded
synthetic/public chunks, then sends only selected context to the configured
provider. Set `EKAA_RAG_ENABLED=true` only alongside enabled PostgreSQL/pgvector
retrieval and an explicitly configured compatible LLM provider.

The endpoint returns an answer, normalized provider usage, and server-derived
document/chunk source metadata. It never accepts source IDs from the model. When
retrieval returns no result above `EKAA_RAG_MIN_EVIDENCE_SCORE`, or none can fit the
configured source/context character/token budgets, EKAA returns the deterministic
insufficient-information response without calling the LLM. Retrieved text and the
question remain untrusted data in the generation prompt. See
`docs/STAGE_4_RAG_DESIGN.md` for configuration, security boundaries, and limits.

## Stage 5 Citation & Grounding

The same `POST /api/rag/answer` endpoint now returns a UI-ready `claims` array and
server-built `citations` array. The configured provider is asked for a small JSON
claim contract with selected chunk IDs, but those IDs are only untrusted proposals:
EKAA validates each ID against the exact retrieval context, verifies a bounded
supporting sentence excerpt, and copies document/chunk/page/section metadata from
the selected source. Public `citation-*` IDs are generated by EKAA, never by the
provider.

If any generated claim has no citation, names a chunk not selected for the request,
or cannot be supported by a source sentence, the endpoint returns the deterministic
insufficient-information answer and marks the claim as `unsupported`. It does not
return a partly unsupported generated answer as fact. This is a deterministic
lexical-entailment baseline, not a semantic judge; valid paraphrases may be rejected.
See `docs/STAGE_5_CITATION_GROUNDING_DESIGN.md` for the exact contract, limits, and
known limitations.

## Stage 6 Hybrid Retrieval

Stage 6 can combine the existing semantic candidates with PostgreSQL full-text
candidates. Apply `alembic upgrade head` to create the PostgreSQL `simple`
full-text GIN expression index, then set `EKAA_HYBRID_RETRIEVAL_ENABLED=true`
alongside `EKAA_EMBEDDING_ENABLED=true`. The candidate counts and RRF rank constant
are bounded configuration values; no model or provider is added.

`POST /api/retrieval/search` remains read-only. With
`{"include_diagnostics": true}`, it can return bounded branch counts only; it
never echoes the query or adds unselected document information. Identifier-shaped
tokens (such as `INC-1042`) get an exact lexical candidate path before deterministic
rank fusion. The same retrieval seam is used by RAG, so citations still derive only
from final selected chunks. See `docs/STAGE_6_HYBRID_RETRIEVAL_DESIGN.md`.

## Stage 7 Reranking

Stage 7 optionally reranks only the already-qualified, bounded hybrid candidates
used to build RAG context. Set `EKAA_RERANKING_ENABLED=true` only together with
enabled hybrid retrieval. The included `deterministic_overlap` provider is local,
reproducible, and has no provider cost; it orders candidates by the fraction of
unique normalized query terms present in each candidate. It is a test/demo baseline,
not a production relevance model.

Reranking never changes retrieval evidence confidence, adds sources, bypasses the
existing context character/token/source limits, or controls citations. A timeout,
provider failure, or malformed reranker response falls back to the original stable
hybrid order. See `docs/STAGE_7_RERANKING_DESIGN.md` for the tested comparison and
limitations.

## Stage 8 RAG Evaluation

Stage 8 provides a versioned 50-question synthetic golden dataset and a controlled
local evaluation runner. It calculates reproducible retrieval metrics, latency,
tokens, failures, unsupported-claim rate, and optional cost only when a versioned
price schedule exists. Correctness, faithfulness, and citation accuracy require
calibrated human assessments; every live replay records them as unavailable. A review
can be applied only afterwards through the offline v2 assessment command, which binds
the review to the exact raw-byte SHA-256 and run ID of one unscored full
local-generation artifact.
It does not call a database or provider by default and its JSON/CSV artifacts exclude
question and document content. The separate `--synthetic-ollama-corpus` path is an
explicit local-only generation evaluation: it accepts only the loopback Ollama
OpenAI-compatible endpoint, requires `--allow-local-ollama`, and maps only the
versioned synthetic corpus through the existing RAG/citation boundaries. It never
opens the database or exposes a HTTP endpoint. With Ollama running locally and an
untracked `.env` containing the commented Stage 8 settings from `.env.example`, an
operator may run a full replay. Before its 50 questions, the command makes exactly
one generic, content-free readiness completion through the same local provider instance.
It emits only a six-field safe outcome record, discards its answer and usage, and aborts
before the runner, JSON/CSV artifacts, or reviewer packet if that completion does not
stop with the configured model. This environmental preparation is excluded from all
evaluation metrics and source artifacts; it does not guarantee later availability.
Single-question smoke runs intentionally do not perform this readiness step.

If a prior policy-bound readiness attempt failed, an operator may first test only the
same bounded environmental prerequisite. This preflight validates the full reviewed
dataset, policy, and approved corpus before provider construction, then makes exactly
one readiness call through its isolated readiness-only provider boundary. It accepts no JSON/CSV paths, review packet, smoke selector, or
assessment input; it never retrieves evidence, runs questions, or writes artifacts.
A ready result does not bypass the normal full-run readiness gate or guarantee a later
provider call will succeed.

```powershell
python -m backend.app.evaluation.cli `
  --dataset docs/evaluation/golden_dataset_v1.json `
  --policy docs/evaluation/baseline_policy_v1.json `
  --local-ollama-preflight `
  --synthetic-ollama-corpus docs/evaluation/synthetic_corpus_v1.json `
  --allow-local-ollama `
  --top-k 5 `
  --run-id baseline-policy-preflight-YYYYMMDD
```

```powershell
python -m backend.app.evaluation.cli `
  --dataset docs/evaluation/golden_dataset_v1.json `
  --synthetic-ollama-corpus docs/evaluation/synthetic_corpus_v1.json `
  --allow-local-ollama `
  --output-json C:\safe-local\stage8_ollama_qwen3_4b.json `
  --output-csv C:\safe-local\stage8_ollama_qwen3_4b.csv `
  --top-k 5 `
  --run-id ollama-qwen3-4b-YYYYMMDD
```

`--top-k` must match `EKAA_RAG_RETRIEVAL_TOP_K`; the default for both is 5. The
result is a measured local run, not a baseline or a quality claim until calibrated
human assessments are supplied. See `docs/STAGE_8_EVALUATION_DESIGN.md` for formulas,
calibration, gates, caveats, and all controlled commands.

For one explicit local synthetic reviewer session, add `--review-packet <outside-repo
Markdown path>` and `--reviewer-id <pseudonymous-id>` to a normal full local Ollama
replay (never a smoke run). The command writes an unscored content-free source artifact
and a separate local Markdown packet from that exact replay. The packet contains only
approved synthetic question/answer/evidence/citation material, must remain outside the
repository, is never a scoring input, and is retained or deleted only by the operator.
It is intended for a reviewer to record verdicts before a separate v2 assessment bundle
is created. A solo reviewer produces provisional human evidence, not inter-reviewer
agreement.

For one local-provider smoke question before a full replay, add
`--question-id <reviewed-question-id>` to that exact Ollama command. The command still
loads and validates the complete dataset and pinned corpus before selecting the one
question, forbids `--assessments`, writes `single_question_smoke` into the artifact,
and cannot be used as a regression baseline or comparison input. Omit
`--question-id` for the normal 50-question `full_dataset` run.

After a reviewer creates a strict content-free v2 assessment bundle for one completed
full local-generation artifact, score it without constructing a target, provider, or
database connection:

```powershell
python -m backend.app.evaluation.cli `
  --dataset docs/evaluation/golden_dataset_v1.json `
  --assessments C:\safe-local\stage8_assessment_v2.json `
  --assessment-source-artifact C:\safe-local\stage8_ollama_full.json `
  --output-json C:\safe-local\stage8_ollama_scored.json `
  --output-csv C:\safe-local\stage8_ollama_scored.csv
```

Offline scoring accepts exactly those two assessment/source inputs and no live target,
Ollama flag, smoke selector, or run ID. Input and output paths must all differ. Legacy,
smoke, fixture, retrieval-only, previously scored, incomplete, or aggregate-forged
artifacts are rejected before either output is written.

## Stages 9–12 Authorization, Tools, and Agent Baseline

Stage 9 applies authorization before protected content can enter retrieval and
records privacy-safe authorization audit events. Stage 10 adds bounded deadlines,
retries, circuit breaking, safe degradation, and dependency health checks.

Stage 11 provides a selection-independent typed `ToolRegistry` with strict input
schemas, per-tool timeouts, read-only integration seams, SSRF-aware allowlisted
HTTPS handling, and structural-only secret-safe audit records. Write-classified
registration has no executable handler; the registry can only build a proposal.

Stage 12 provides an explicit provider-neutral single-agent state machine with hard
step/retry/repetition/message limits, checkpoint and principal binding, fresh
authorization on resume, hostile-output handling, and terminal proposal states.
LangGraph is not currently a dependency; the rationale and migration boundary are
recorded in `docs/adr/ADR-010-provider-neutral-agent-state-machine.md`. The local
contract baseline and its recorded 3/3 result are in
`docs/evaluation/stage12_baseline_v1.json`.

These stages are local reference implementations, not production IAM, enterprise
SLO, multi-agent, or production-quality claims. Stage 13/14 approval state is an
in-memory local reference and requires a durable transactional store and manual
reconciliation before any real external write. Stage 14 metrics require complete
scenario/trace mapping, calibrated human review for ambiguous cases, and a versioned
provider price schedule before cost can be reported.

## Quality Checks

```powershell
pytest
ruff check .
ruff format --check .
mypy backend
```

See `AGENTS.md` before making changes, `ROADMAP.md` for stage gates, and
`docs/PLAN_REVIEW.md` for the review of the original starting plan.
