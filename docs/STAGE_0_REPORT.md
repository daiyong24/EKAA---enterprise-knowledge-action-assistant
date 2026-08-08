# Stage 0 Report

**Goal:** Establish the source of truth and a verified backend engineering
foundation without implementing future-stage functionality.

**Current branch:** `main`

**Repository status:** New Git repository initialized. All project files are
untracked and intentionally uncommitted; no remote is configured and no publish or
commit action was requested.

## Files Created

- Governance and product docs: `AGENTS.md`, `ARCHITECTURE.md`,
  `BUSINESS_RULES.md`, `ROADMAP.md`, `README.md`, `docs/PLAN_REVIEW.md`
- Project/configuration: `pyproject.toml`, `.env.example`, `.gitignore`,
  `.pre-commit-config.yaml`, `.github/workflows/ci.yml`
- Backend: package initialization plus `backend/app/main.py`, API router and health
  route, settings, logging, and exception modules
- Tests: shared AnyIO configuration, unit settings tests, and integration health and
  exception-contract tests

**Files modified:** None existed before Stage 0.

## Implementation

- Application factory and FastAPI process entry point
- `GET /health` with an explicit Pydantic response contract
- Environment-prefixed settings loaded from environment or `.env`
- Production guard against debug mode
- Consistent logging baseline that avoids logging unknown exception messages
- Stable public error envelope for expected and unexpected application failures
- Python 3.12 packaging and isolated local virtual environment
- Pytest/coverage, Ruff lint/format, strict mypy, pre-commit, and GitHub Actions CI

## Architecture Decisions

- Use `backend/app` as the package root and keep HTTP routes separate from core
  cross-cutting foundations.
- Delay SQLAlchemy, Alembic, Docker, and all AI dependencies until a stage actually
  requires them.
- Treat security, privacy, evaluation fixtures, and telemetry readiness as
  cross-cutting work with later dedicated hardening stages.
- Introduce PostgreSQL metadata persistence in Stage 2 and pgvector in Stage 3.
- Require minimum retrieval-derived source traceability in Stage 4; Stage 5 adds
  claim-level citation validation and presentation.

## Tests and Results

Executed with Python 3.12.0:

```text
pytest --cov=backend --cov-report=term-missing
6 passed; 100% statement and branch coverage

ruff check .
PASS

ruff format --check .
PASS

mypy backend
PASS — no issues in 11 source files

pre-commit run against every project file
PASS — Ruff, YAML, EOF, whitespace, and large-file checks
```

## Manual Verification

Started the application with Uvicorn on localhost and sent a real HTTP request:

```json
{"status_code": 200, "body": {"status": "ok"}}
```

## Known Limitations

- The GitHub Actions workflow has not run remotely because the repository has no
  GitHub remote and has not been pushed. Equivalent commands pass locally.
- There is no dependency lock file yet; reproducible application image locking is
  deferred until the container/deployment workflow is selected.
- `/health` is process liveness only because Stage 0 has no external dependencies.
- Correlation IDs and JSON logs are deferred until request lifecycle requirements
  exist; the current format is consistent key-value text.
- No commit was created, preserving user control over the initial history.

## Security Review

- No secrets or provider settings exist; `.env` and virtual environments are ignored.
- Debug mode is rejected in production configuration.
- Unexpected public errors do not expose exception text, paths, or stack traces.
- Unexpected exception messages are not copied into logs.
- GitHub workflow permissions are read-only.
- Only synthetic/public content may be introduced before authorization is complete.

## Regression and Scope Review

The full relevant suite passed after the final fixes. No LLM, RAG, embeddings,
database, document ingestion, frontend, authentication, agent, or Docker code was
introduced. Dependencies are limited to the Stage 0 runtime and development checks.

## Acceptance Criteria

- Backend starts: **PASS**
- `/health` works: **PASS**
- Tests pass: **PASS**
- Lint and formatting pass: **PASS**
- Type checking passes: **PASS**
- Basic CI workflow defined with matching checks: **PASS**
- Remote CI observed: **NOT RUN — no remote repository**

**Stage 0 result: PASS locally.** Remote CI observation remains a repository-publish
step, not authorization to begin Stage 1 by itself.

**Next stage:** Stage 1 — LLM Foundation, only after explicit user approval.

No next-stage implementation performed.
