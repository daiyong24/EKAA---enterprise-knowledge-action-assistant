# Stage 1 Report

**Goal:** Add a provider-neutral, testable LLM application boundary without
implementing retrieval, enterprise factual answering, tools, persistence,
authentication, or chat memory.

**Current branch:** `main`

**Repository status:** Stage 1 files are intentionally uncommitted. The pre-existing
GitHub `main` branch was fast-forwarded before implementation to include the project
agent library; no Stage 1 commit, push, branch switch, or remote mutation occurred.

## Files Created

- `backend/app/llm/`: provider protocol, OpenAI-compatible adapter, normalized
  models, prompt builder, service, and typed internal errors
- `backend/app/api/routes/chat.py`
- `tests/unit/test_chat_service.py`
- `tests/unit/test_openai_compatible_provider.py`
- `tests/integration/test_chat.py`
- `docs/STAGE_1_PROVIDER_DECISIONS.md`

## Files Modified

- `backend/app/main.py`, `backend/app/api/router.py`, and
  `backend/app/core/config.py`
- `pyproject.toml`, `.env.example`, `README.md`, `AGENTS.md`, and `ROADMAP.md`

## Implementation

- `POST /api/chat` accepts one bounded stateless message and returns a stable
  structured response: answer, model, and normalized token usage.
- `LLMProvider` prevents infrastructure/provider types from leaking into the API.
- `OpenAICompatibleProvider` has an explicit HTTP boundary, runtime-only secrets,
  configured model name, validated URLs, strict response mapping, and no SDK lock-in.
- `ChatService` builds a non-retrieval safety prompt and applies bounded retries
  only to typed transient failures.
- A deterministic Stage 1 response policy permits only clearly conceptual questions;
  it refuses entity, status, policy, and organization-scoped questions before a
  provider call. It withholds all ungrounded provider text while retaining the
  provider's normalized model and usage data for integration verification.
- Public provider errors are safe, stable envelopes. Logs record only event,
  attempt, and error class—never prompt content, provider payloads, or secrets.

## Architecture Decisions

- The API route only maps HTTP transport and safe error contracts; orchestration is
  isolated in `ChatService`, and the external provider is behind a protocol.
- LLM stays disabled by default. A configured model and API key are required to
  enable it. Production requires an HTTPS provider URL.
- Stage 1 uses no retrieval. Its prompt explicitly refuses to present enterprise
  knowledge claims without the future authorized knowledge base.

## Tests and Results

Executed locally with Python 3.12:

```text
pytest --cov=backend --cov-report=term-missing
27 passed; 91% total coverage

ruff check .
PASS

ruff format --check .
PASS

mypy backend
PASS
```

Tests use deterministic provider fakes and `httpx.MockTransport`; no paid or live
provider call is made. Coverage is informational because no coverage threshold is
currently configured.

## Manual Verification

`POST /api/chat` was verified through FastAPI's in-process ASGI transport with a
deterministic fake provider. A fake answer containing an unsupported Project Orion
deployment claim was not exposed; normalized model and usage values were retained.
An unknown `secret` request field returned only the sanitized `invalid_request`
envelope. The live-provider path was deliberately not manually called because no
provider credentials or paid request is required for Stage 1.

## Known Limitations

- No model/provider has been selected for real deployment; cost, DPA, data-use,
  retention, and residency decisions are not yet made.
- This is an LLM integration sandbox, not a user-facing answer feature: provider
  text is intentionally withheld until the Stage 4 grounded-generation pipeline.
- Token usage is trusted only as provider-reported accounting; billing reconciliation
  and budgets are future work.
- No streaming, cancellation API, rate limiting, circuit breaker, telemetry,
  retrieval, citation, document storage, auth, tools, or chat memory exists.

## Security Review

- Secrets are modeled as `SecretStr`, read only from runtime settings, excluded from
  docs/example values, and never written to log calls or public errors.
- Input is bounded and treats unknown request fields as invalid.
- The prompt treats user input as untrusted and disallows requests for hidden
  prompts, secrets, tools, or unsupported enterprise evidence.
- Request-validation responses are a stable, sanitized `invalid_request` envelope;
  they do not echo rejected input such as secrets or oversized messages.
- Independent business review reproduced secret-field and oversized-input validation
  failures and confirmed the sanitized response. It also confirmed that even a fake
  provider's unsupported enterprise claim is withheld from the public API.
- Real enterprise content remains prohibited until Stage 9 authorization work.

## Regression and Scope Review

Stage 0 health and safe-error tests remain included in the passing suite. No Stage
2+ database, ingestion, embedding, retrieval, auth, tool, agent, frontend, or
chat-memory implementation was added.

## Acceptance Criteria

- Stable `POST /api/chat` API contract: **PASS**
- Provider-neutral, mockable boundary and one implementation: **PASS**
- No paid provider calls in tests; deterministic mocks: **PASS**
- Provider/cost/privacy assumptions documented: **PASS**
- Tests, lint, formatting, and strict typing: **PASS**

**Stage 1 result: PASS locally and independent review PASS.**

**Next stage:** Stage 2 — Document Ingestion, only after explicit user approval.

No next-stage implementation performed.
