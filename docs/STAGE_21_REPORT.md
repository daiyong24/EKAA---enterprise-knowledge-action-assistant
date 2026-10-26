# Stage 21 Report

## Goal

Demonstrate an isolated local synthetic-data path through upload, versioned embedding
maintenance, pgvector retrieval, and Ollama-backed RAG with a supported citation.

## Current branch

`main`; no commit or push was performed.

## Repository status

Stage 21 implementation is in the working tree. The existing `.env` was not read or
modified. After verifying that it did not exist, a Git-ignored `.env.stage21` was
created from the template with a random local database password; the password was
not emitted. The dedicated Compose project `ekaa-stage21-verify-20261002` on port
`18021` remains present. Its database volume and rows were preserved throughout the
run. The API container was recreated within that project to apply the Stage 21 v2
configuration and selected Ollama model; the database service/volume was not
recreated or removed.

## Files created

- `compose.stage21.yaml`
- `.env.stage21.example`
- `docs/fixtures/stage21_synthetic_recovery.md`
- `docs/STAGE_21_LOCAL_DEMO.md`
- `docs/STAGE_21_REPORT.md`
- `docs/exec-plans/active/stage21_local_backend_demo.md` (local active execution
  record; intentionally ignored until acceptance is complete)
- `scripts/stage21-local-demo.ps1`
- `tests/unit/test_stage21_local_demo.py`

## Files modified

- `.gitignore`: ignores `.env.stage21` and `.env.stage21.local`.
- `AGENTS.md`, `ROADMAP.md`, and `README.md`: describe active Stage 21.
- `compose.stage21.yaml`: selects deterministic hash v2 and bounded local model
  timeouts; the base `compose.yaml` is unchanged.
- `backend/app/embeddings/provider.py`: preserves v1 semantics and adds v2 word
  tokenization, rejecting unknown deterministic model IDs.
- `scripts/stage21-local-demo.ps1`: restricts Compose project names to the dedicated
  `ekaa-stage21-` namespace, preventing use of the default `ekaa` project.
- `tests/unit/test_embeddings.py`: pins the v1 vector and tests v2 similarity,
  normalization, repeatability, and unsupported IDs.
- `docs/STAGE_3_EMBEDDING_RETRIEVAL_DESIGN.md`: documents versioned tokenizer rollout.
- `tests/unit/test_stage21_local_demo.py`: verifies Stage 21 overlay and script
  contracts.

## Implementation

`deterministic-hash-v1` keeps the original tokenization/fallback and remains the
general settings and `.env.example` default for compatibility with persisted rows.
`deterministic-hash-v2` uses the corrected Unicode `\w` token pattern and is chosen
only by the Stage 21 overlay. Rows coexist by model ID and dimensions; the v1 row was
not overwritten. The demo recreates only its API container, runs pending maintenance
through `docker compose exec -T` (no one-off container removal), verifies v2 coverage
for all completed chunks, then checks retrieval score and citation identity.

The overlay keeps the API bound to `127.0.0.1` and PostgreSQL on its existing
internal network without a published DB port. Only the API joins the additional
Docker bridge to reach host Ollama. A normal bridge may also permit general outbound
connectivity according to Docker Desktop and host firewall policy; this is not an
Ollama-only egress firewall. Local generation exceeded the generic 15-second provider
timeout, so the Stage 21 overlay uses one request up to 60 seconds, a 90-second RAG
deadline, and zero automatic provider retries. General defaults are unchanged. This
development-only overlay does not alter the application or production architecture;
no ADR was required.

## Tests

- Embedding and Stage 21 focused tests: **16 passed**.
- Full test suite, first run: **4 failed, 409 passed, 9 skipped** because the new
  runbook linked to `STAGE_21_REPORT.md` before the report existed. The four failures
  were Stage 20 portfolio link-validation preconditions, not product regressions.
- Full test suite after adding this report and applying the review corrections:
  **413 passed, 9 skipped** (`.venv\Scripts\pytest.exe -q`). The skips require a
  disposable PostgreSQL test URL or Windows symlink privileges unavailable here.
- Full Ruff lint: passed.
- Full Ruff format check: passed (240 files).
- Mypy: passed (117 backend source files).
- Compose merged config validation: passed using the isolated Stage 21 env/project.
- PowerShell parser: passed.
- `.env.stage21` Git ignore check: passed.
- `git diff --check`: passed with LF-to-CRLF advisory warnings.

## Test results

Initial v1 live run against the synthetic fixture returned the correct document/chunk
identity but cosine score `0.0`; RAG correctly returned insufficient information
because the score was below the `0.2` evidence threshold. After the v2 rollout, the
same fixture returned score **0.6172133868102251** for model
`deterministic-hash-v2`, above the threshold. The isolated database contains one
completed chunk, with **1 v1 row and 1 v2 row**; the v2 row covers **1/1 completed
chunks**. The fixture document/chunk IDs remained stable.

RAG did not pass for any tested local model:

- `qwen3:4b` with the generic 15-second provider timeout logged
  `ProviderTimeoutError`; the public route maps the resulting unavailable condition
  to HTTP 503 `rag_generation_unavailable` (the initial script version suppressed the
  response status/code).
- `qwen3:8b` under the Stage 21 60-second/90-second bounded settings completed
  generation, but the citation validator logged
  `rag_generation_rejected_unsupported_claim`; the API safely returned its refusal
  result and the required supported-citation assertion failed.
- The one approved final `qwen3:4b` run under the same bounded settings also returned
  a response that failed the supported-claim-to-fixture-citation assertion; its
  isolated API log recorded `rag_generation_rejected_unsupported_claim`.
- `gemma3:4b` and `qwen2.5:3b` both returned the safe public HTTP 502 code
  `rag_invalid_generation` under the same bounded Stage 21 configuration; raw output
  and provider payload were suppressed.

No prompt or raw provider payload was inspected or printed. These four model tags
confirm v2 retrieval and safe failure, but do not establish a successful local RAG
answer for the currently installed model set.

After the user authorized launching the Ollama desktop application, a separate,
plan-reviewed diagnostic used `qwen3:4b` and one in-memory copy of the exact synthetic
G001 fixture. It ran exactly three sequential calls through the normal `RagService`,
JSON output parser, and citation validator (60-second provider timeout, 90-second RAG
deadline, zero retries):

- Generic prompt, no response-format control: safe `RagUnavailableError` after 21.6s.
- Generic prompt, `json_object` response format only: safe `RagUnavailableError` after
  60.2s.
- Direct-evidence prompt, no response-format control: safe `RagUnavailableError` after
  60.2s.

The diagnostic did not produce an accepted claim or a distinguishable parser or
validator result, so it cannot attribute the original failures to either prompt
profile or response format. No prompt/output text was inspected, no database or
Compose state was changed, and no fourth model call was made. No prompt/config
adjustment is justified by these outcomes; live RAG acceptance remains failed.

On 2026-10-27, after Docker Desktop was started, the existing isolated Compose
project was checked without changing its database or configuration. The API and
database containers were healthy, and the API health endpoint returned success.
The retrieval endpoint returned one source with the expected v2 score
`0.6172133868102251` and a stable document/chunk identity. One real API RAG request
then reached the configured local model `qwen2.5:3b`: Ollama returned HTTP 200 in
approximately 4.5 seconds, but EKAA returned safe public HTTP 502
`rag_invalid_generation`. The API log confirms the failure occurred after the
provider response and before a valid RAG response was returned; no prompt, raw
model output, or provider payload was inspected. This run narrows the current
failure from provider unavailability to provider-output normalization/parsing for
this model, but the exact rejected field or response shape remains unknown.

On the same date, a follow-up content-safe provider diagnostic isolated the rejection
without printing or persisting model content. The `qwen2.5:3b` response had the
expected OpenAI-compatible structure, valid JSON with the exact top-level `claims`
key, and one parsed claim. The citation parser accepted that JSON, but the validator
classified the claim as unsupported with reason code `citation_not_selected`: the
provider proposed a chunk ID that was not among the server-selected context IDs for
that request. The immediate root cause for this run is therefore incorrect
provider-selected citation identity, not Ollama transport, JSON decoding, usage
normalization, retrieval failure, or a missing answer. The server-owned citation
validator correctly rejected the claim and preserved safe failure behavior. No raw
claim text or proposed ID was exposed.

A plan-reviewed four-case diagnostic then held the model (`qwen2.5:3b`), question,
selected synthetic context, evidence score, context limits, timeout, deadline, and
zero retries constant while changing prompt/format controls one case at a time:

| Case | Prompt | JSON response format | Safe result |
| --- | --- | --- | --- |
| A | Generic | Off | `unsupported_claim`; `citation_not_selected` |
| B | Direct-evidence | Off | `supported_selected_citation` |
| C | Generic | On | `RagOutputError` |
| D | Direct-evidence | On | `supported_selected_citation` |

The matrix ran sequentially and stopped after exactly four calls. No prompt, raw
answer, claim text, full chunk ID, provider payload, or secret was printed or
persisted. With the same selected context, the generic path reproduced the current
failure and the direct-evidence profile passed with a server-created citation. This
supports the direct-evidence prompt as the minimal candidate fix for this Stage 21
failure class; it does not prove corpus-wide generalization or pass the real API
configuration gate yet. The citation validator remained unchanged.

The approved Stage 21-only wiring was then implemented: `Settings` now has the
default-off internal flag `rag_direct_evidence_profile`, the application factory
passes it to `RagPromptBuilder`, and only `compose.stage21.yaml` enables it. The
base Compose file, global defaults, Stage 8 evaluation path, citation validator,
retrieval, database schema, and public API contract were not changed. Focused tests
and the full suite pass (`415 passed, 9 skipped`); Ruff, format, mypy, Compose
configuration, PowerShell parsing, and diff checks pass.

The rebuilt isolated API container was healthy and retained the existing database
service/volume. Retrieval still returned the fixture with score
`0.6172133868102251`. One live request reached Ollama and the API route returned
HTTP 200, but its response body was not retained by the safe diagnostic harness; a
subsequent request exceeded the bounded generation deadline and returned safe 503
`rag_generation_unavailable`. Therefore a supported claim with a server-created
fixture citation was not independently verified through the real API, and Stage 21
acceptance remains **FAILED / UNVERIFIED** pending a successful captured-safe live
response and independent review.

An additional bounded outcome check on 2026-10-27 confirmed the direct-evidence flag
was active but produced the same runtime failure: `qwen3:4b` reached the 60-second
provider boundary, Ollama returned HTTP 500, and the API returned safe 503
`rag_generation_unavailable`. A second matching 60-second HTTP 500/503 pair is
visible in the short log window. This supports local qwen3 inference instability at
the provider boundary as the remaining immediate blocker, but does not identify the
internal GPU/runner subtype or justify changing timeout/model configuration yet.

## Manual verification

Historical pre-model-switch verification exercised the full local path and recorded
safe failures for the originally tested models. After the approved model switch, the
isolated Stage 21 project was rerun end to end: upload, API/DB readiness,
`embed-pending`, v2 coverage, retrieval identity/score, and the supported
claim/citation step all passed with `qwen2.5:3b`. The PowerShell script reported
`Stage 21 local backend demo passed`.

## Known limitations

The accepted result covers one synthetic fixture and one local model path only. The
synthetic hash provider is a deterministic feature hash, not a semantic embedding
model or a retrieval-quality claim. Local model wording and latency vary. No
corpus-wide quality, throughput, production security, or SLO benchmark is claimed.

## Security review

The existing `.env` was not read or changed. The local password is kept only in the
ignored `.env.stage21`; it was not printed. PostgreSQL is private on the Compose
internal network, the API port is loopback-only, and the Docker bridge has the
outbound-scope limitation stated above. The dataset is synthetic. No container,
database volume, or database row was removed; v1 rows remain available for rollback.
The demo script also rejects Compose project names outside the `ekaa-stage21-`
namespace before it starts or recreates services.

## Regression review

No database schema, API route, RAG/citation rules, general embedding default, or base
Compose service/network was changed. V1 vector output is pinned by a golden test;
v2 is selected by a distinct model ID. Existing versions remain queryable by choosing
their matching model ID.

## Independent review

- Architecture review: **PASS**; no boundary or deployment-architecture issue found.
- Business review: **PASS**; synthetic-only, grounding/citation, safe-error, and local
  data boundaries are preserved.
- Database review: **PASS** after restricting the demo Compose project name to the
  `ekaa-stage21-` namespace; existing v1/v2 rows and the volume are preserved.
- Earlier pre-model-switch code review: **CHANGES_REQUIRED** because the live RAG
  acceptance gate failed before the model-selection fix.
- Earlier pre-model-switch final verification: **FAILED** for the same historical
  live RAG blocker.

The later model-selection implementation review found two minor completeness gaps:
the script default-model contract lacked a focused assertion, and the earlier review
status was not reconciled with the successful model-switch validation. The script
assertion was added and the final model-switch review was recorded as **PASS**; no
runtime or business-rule defect was found.

- Final model-switch code/business review: **PASS**.
- Final acceptance verification: **PASS**; the supported synthetic claim and
  server-created citation were verified through the isolated API.

The implementation/review log is retained in the local active execution record under
`docs/exec-plans/active/`; it is intentionally ignored by Git until completion.

## Model-selection fix and live verification

Based on the bounded evidence that `qwen3:4b` repeatedly failed at the 60-second
Ollama boundary while `qwen2.5:3b` passed the direct-evidence in-memory matrix, the
isolated Stage 21 default was changed to `qwen2.5:3b`. The direct-evidence profile,
60-second provider timeout, 90-second RAG deadline, zero retries, network boundary,
and database state were unchanged. The base Compose file, global defaults, Stage 8,
and production configuration were not changed.

The isolated API container was recreated without recreating the database container
or volume. The normal demo script then passed end to end: API/DB health, fixture
upload/idempotency, v2 embedding coverage, retrieval identity and score, and one
bounded real RAG request. Safe live result:

- model: `qwen2.5:3b`
- fixture retrieval score: `0.617213386810225`
- retrieved fixture chunks: `1`
- supported claims citing the fixture: `1`
- server-created citation: verified

The script reported `Stage 21 local backend demo passed`. No raw prompt, model
answer, provider payload, or secret was recorded.

## Acceptance criteria

**PASS:** The isolated services, upload, embedding, v2 coverage, retrieval identity,
retrieval score, and required supported RAG claim with a server-created citation to
the synthetic fixture all passed using the isolated `qwen2.5:3b` Stage 21 target.
This is a local synthetic demo result, not a production-quality, throughput, SLO, or
corpus-wide quality claim.

## Next stage

No next-stage implementation performed.
