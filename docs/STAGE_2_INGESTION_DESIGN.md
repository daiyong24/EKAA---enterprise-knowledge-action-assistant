# Stage 2 Ingestion Design

Stage 2 accepts only synthetic or explicitly public PDF, UTF-8 TXT, and UTF-8
Markdown. It performs this synchronous, atomic flow:

```text
multipart upload -> filename/MIME/size/signature validation -> parse
-> Unicode/whitespace normalization -> deterministic chunks -> one transaction
-> documents + document_chunks
```

`POST /api/documents` accepts exactly one multipart field named `file`; extra fields
or extra files are rejected. Before any multipart parser runs, an ASGI upload
boundary enforces `EKAA_INGESTION_MAX_REQUEST_SIZE_BYTES` on the whole HTTP body.
It rejects an excessive declared length and counts streamed bytes when the header is
missing, malformed, or falsely small, so multipart parsing cannot spool an
unbounded request. The separate file-byte limit remains a defence in depth. The
extension and declared MIME type must agree exactly with the small supported
allow-list. Path-like, traversal-like, NUL-containing, oversized, binary-text,
empty, encrypted, and malformed inputs are rejected. Public errors contain no
filename, content, path, checksum, parser, or database detail. Logs include only
the failure event and safe format/error class.

Each document stores its original upload filename, approved MIME type, SHA-256 of
the input bytes, `completed` status, created/completed timestamps, and minimal
format/size metadata. The original file bytes are not retained. Each chunk stores
its document ID, zero-based order, normalized text, SHA-256 checksum, page number
for PDFs or nearest heading section for Markdown, and created timestamp. Markdown
sections use a text column, so a valid long heading is preserved losslessly.

A unique document checksum implements idempotency. A replay returns the original
metadata with HTTP 200 and `duplicate: true`; the initial committed ingestion
returns HTTP 201. A unique `(document_id, chunk_index)` constraint protects chunk
ordering. Insert and chunk creation share one SQLAlchemy transaction. Parse failures
occur before persistence; database failures roll back the whole transaction.

PDF extraction is bounded by `EKAA_INGESTION_MAX_PDF_PAGES` and
`EKAA_INGESTION_MAX_EXTRACTED_CHARACTERS`; exceeding either produces the same safe
parse-failure contract and no persisted records.

SQLAlchemy 2 models and the Alembic migration are PostgreSQL-capable. Local
development defaults to SQLite only for convenience. Before local uploads, set
`EKAA_DATABASE_URL` as appropriate and run:

```powershell
alembic upgrade head
```

Dependencies added for this stage:

- `SQLAlchemy`: database models, transaction boundary, and replaceable session factory.
- `Alembic`: versioned relational schema migration.
- `psycopg[binary]`: PostgreSQL driver for the documented PostgreSQL deployment path.
- `PyMuPDF`: bounded PDF text extraction; no OCR is enabled.
- `python-multipart`: FastAPI multipart upload parsing.

Stage 2 deliberately has no original-file storage, background queue, OCR,
embeddings, vector columns/search, retrieval, LLM response changes, authentication,
or authorization. It must not be used with confidential enterprise content before
Stage 9 authorization controls exist.
