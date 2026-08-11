# Stage 2 Report

**Goal:** Reliably ingest synthetic/public PDF, TXT, and Markdown into normalized,
traceable relational chunks without adding embeddings, retrieval, or authorization.

**Current branch:** `main`

**Repository status:** Stage 2 changes are uncommitted; no Git/remote operation was
performed by the implementation role.

## Files Created

- `backend/app/db/` SQLAlchemy base, document/chunk models, and session construction.
- `backend/app/ingestion/` validation, parsing, normalization, chunking, repository,
  service, and typed errors.
- `backend/app/api/routes/documents.py`.
- `alembic/`, `alembic.ini`, and the document-ingestion migration.
- `tests/unit/test_ingestion_pipeline.py` and
  `tests/integration/test_document_ingestion.py`.
- `docs/STAGE_2_INGESTION_DESIGN.md`.

## Files Modified

- `AGENTS.md`, `ROADMAP.md`, `README.md`, `.env.example`, `pyproject.toml`,
  `backend/app/core/config.py`, `backend/app/main.py`, and
  `backend/app/api/router.py`.

## Implementation

- `POST /api/documents` accepts exactly one multipart `file`, rejects extra fields
  and files, validates narrow extension, MIME, filename, size, and basic content
  signatures, then returns a stable safe response.
- An ASGI request boundary bounds the entire upload before multipart parsing,
  including requests with absent, malformed, or forged `Content-Length`; a separate
  file-byte read cap remains in effect.
- PyMuPDF extracts PDF page text; TXT requires UTF-8; Markdown produces heading
  section metadata. Unicode NFC and bounded whitespace normalization precede
  deterministic character chunking.
- Documents and chunks are persisted atomically with checksums, timestamps, status,
  filename/MIME, page/section where available, chunk index, and minimal metadata.
- A unique document checksum returns a previously committed document on replay;
  parse and storage failures do not leave partial records.
- PDF extraction has configured maximum-page and cumulative-character limits.
  Markdown section provenance is stored in an unbounded text column.

## Architecture Decisions

- Route handlers own only multipart transport and public error mapping. The pure
  pipeline is independent of FastAPI; `IngestionService` owns orchestration and the
  repository/session boundary owns persistence.
- SQLAlchemy 2 and Alembic support PostgreSQL URLs. SQLite remains a local
  development convenience and isolated test backend, not a production selection.
- Original file bytes are not persisted, reducing retention scope while retaining
  normalized chunks needed by later, authorized retrieval stages.

## Tests

The Stage 2 suite uses temporary isolated SQLite databases and synthetic documents;
it never invokes a production database, provider, or enterprise document source.
It covers valid TXT/Markdown/PDF, corrupt PDF, invalid UTF-8, blank content,
duplicate replay, extension/MIME mismatch, unsupported/traversal-like filenames,
file/request size limits (including missing/forged `Content-Length`), exactly-one
multipart enforcement, safe API errors including storage `503`, transaction
rollback of both documents and chunks, deterministic checksum-race replay, chunk
ordering/checksums, long section metadata, and PDF extraction limits.

## Test Results

Executed locally with Python 3.12:

```text
pytest --cov=backend --cov-report=term-missing
51 passed; 92% total coverage

ruff check .
PASS

ruff format --check .
PASS

mypy backend
PASS

alembic upgrade head (isolated temporary SQLite URL)
PASS; revision 20260812_01 applied

pre-commit run --files <all Stage 2 files>
PASS
```

## Manual Verification

The multipart API was exercised through FastAPI's in-process ASGI transport using
temporary isolated SQLite databases. Valid synthetic TXT, Markdown, and generated
PDF uploads returned `201`; a byte-identical replay returned `200` and the original
document ID. The migration was separately applied to a temporary SQLite file and
reported revision `20260812_01`. Additional manual-boundary-equivalent ASGI tests
confirmed that missing and falsely small `Content-Length` values cannot bypass the
request-size cap.

## Known Limitations

- Uploads are synchronous and bounded in memory; no job queue, original-file
  storage, OCR, or broad-format support exists.
- The application does not auto-apply migrations; operators must run Alembic.
- No authentication/authorization exists, so real confidential enterprise content
  remains prohibited until Stage 9.

## Security Review

- The allow-list rejects path-like filenames, type mismatches, oversized bytes, and
  binary text. No untrusted file path is opened; PDFs are parsed from bytes only.
- Public API errors and diagnostic logging omit uploaded content, filenames,
  checksums, filesystem paths, parser exceptions, and SQL details.
- No upload bytes are persisted; logs contain only safe event fields, including the
  content-free `document_request_too_large` event for pre-parser body-limit rejects.

## Regression Review

Stage 0 and Stage 1 APIs/tests remain in the test suite. No embedding, vector,
retrieval, LLM-answer, auth, frontend, tool, agent, Docker, OCR, or live-enterprise
data capability was added.

## Acceptance Criteria

- Repeatable ingestion with traceable metadata and no embeddings: **PASS**
- Safe failure with no partial document/chunk state: **PASS**
- PDF, TXT, Markdown, idempotency, migration, and required checks: **PASS**

**Stage 2 implementation result: PASS locally and independent code/business review
PASS.**

**Next stage:** Stage 3 — Embeddings + Vector Retrieval, only after explicit user
approval.

No next-stage implementation performed.
