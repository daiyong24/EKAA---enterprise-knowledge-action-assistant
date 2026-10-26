# Stage 21 — Local Backend End-to-End Demo

## What this demonstrates

This local demo sends one fictional Markdown fact through the existing EKAA backend:

```text
synthetic Markdown upload
  → PostgreSQL document/chunk storage
  → deterministic embedding maintenance
  → pgvector retrieval
  → local Ollama generation
  → server-validated supported claim and citation
```

The fixture states that the synthetic demo recovery target is **47 minutes**. It is
invented demonstration data and does not describe a real organization or service.
The demo exercises the actual HTTP/API, database, embedding-maintenance command,
retrieval, and RAG boundaries. Generation wording and performance depend on the
local model and hardware; the script checks that the cited claim belongs to the
uploaded fixture, not that the model produces a particular sentence.

## Prerequisites

- Windows PowerShell 5.1 or newer, Docker Desktop, and a working `docker` CLI.
- Ollama listening on the Windows host at `127.0.0.1:11434`.
- The selected model already installed in Ollama. The default is `qwen2.5:3b`; use
  `-Model qwen3:4b` or another installed Ollama tag for a deliberate diagnostic.
- A free local API port (default `8021`).

No provider cloud account or API key is used. The OpenAI-compatible API key value in
the local env file is a placeholder accepted by local Ollama; never put a real key
in this demo file.

## Environment and network boundaries

Copy `.env.stage21.example` to `.env.stage21` and edit its local-only database
values. `.env.stage21` is ignored by Git and is not the existing `.env`. The demo
invokes Compose with the explicit Stage 21 env file and project name
`ekaa-stage21-demo` by default. Override the project name if you need a separate
stack, but keep the required `ekaa-stage21-` prefix; the script rejects other
project names so it cannot start services or migrations in the default `ekaa`
project. Do not point this project at an existing database or reuse another
Compose project.

The overlay retains the base Compose protections. The API port publishes only on an
IPv4 loopback address. PostgreSQL has no published host port and stays on the base
internal `backend` network. The Stage 21 overlay adds a bridge network to the API
only so it can connect to `host.docker.internal:11434`; it does not attach PostgreSQL
to that bridge. A normal Docker bridge may also provide the API container general
outbound connectivity according to Docker Desktop and host firewall policy; the
overlay does not claim an Ollama-only egress firewall rule. Embeddings use the
versioned local deterministic hash v2 tokenizer. Hybrid search and reranking remain
disabled.

## One-command demo

From the repository root:

```powershell
Copy-Item .env.stage21.example .env.stage21
# Edit .env.stage21 database values in a local editor; do not paste them into chat.
.\scripts\stage21-local-demo.ps1
```

PowerShell can also run the script in one path expression:

```powershell
.\scripts\stage21-local-demo.ps1 -ApiPort 8022 -Model qwen3:8b
```

The script rejects non-loopback hosts. It checks the Ollama tags endpoint and model,
starts only the isolated Stage 21 `db` and `api` services, checks API and database
readiness, uploads the fixture (checksum duplicates are successful/idempotent), runs
`embed-pending` in the existing API container (no one-off container is removed),
verifies v2 vectors cover every completed chunk in that isolated database, confirms
retrieval returns the uploaded document, then verifies that RAG returned at least
one supported claim citing that document and retrieved chunk. It suppresses
Compose/provider command output that
could contain configuration details. It never stops containers or deletes volumes.

Expected final output includes the local API URL, synthetic document ID, number of
retrieved fixture chunks, number of supported fixture citations, selected local
model, and a notice that the database volume remains. It does not print database
credentials or the provider API-key setting.

The overlay allows one local provider request up to 60 seconds within a 90-second
RAG request deadline, with automatic provider retries disabled for this demo. These
bounded values were chosen after the default 15-second provider timeout produced a
confirmed `ProviderTimeoutError` on the local run; they are not production latency
targets. If the selected model still exceeds the limit, the script reports only the
stable public RAG HTTP status/error code and does not emit provider diagnostics.

## Manual Swagger walkthrough

Start the same isolated services without running assertions:

```powershell
docker compose --env-file .env.stage21 -f compose.yaml -f compose.stage21.yaml `
  --project-name ekaa-stage21-demo up -d db
docker compose --env-file .env.stage21 -f compose.yaml -f compose.stage21.yaml `
  --project-name ekaa-stage21-demo up -d --build --force-recreate api
```

Open `http://127.0.0.1:8021/docs` and:

1. Call `GET /health` and expect `200` with `{"status":"ok"}`.
2. Use `POST /api/documents` and upload
   `docs/fixtures/stage21_synthetic_recovery.md` as the `file` multipart field.
3. In a PowerShell terminal at the repository root, embed newly uploaded chunks:

   ```powershell
   docker compose --env-file .env.stage21 -f compose.yaml -f compose.stage21.yaml `
     --project-name ekaa-stage21-demo exec -T api `
     python -m backend.app.embeddings.cli embed-pending
   ```

4. Call `POST /api/retrieval/search` with
   `{"query":"What is the synthetic demo recovery target?","top_k":5}`.
   Confirm a returned source has the uploaded document ID and contains the synthetic
   fact.
5. Call `POST /api/rag/answer` with
   `{"question":"What is the synthetic demo recovery target?"}`. Confirm at least
   one claim is `supported` and one returned citation names the uploaded document
   and a chunk from retrieval.

The global `.env.example` intentionally remains on `deterministic-hash-v1` for
existing installations. For a live Stage 21 demo, the overlay selects v2 and the
script recreates only its API container; all existing v1 rows remain in the same
database. For a v1-only database, run the maintenance process with a temporary v2
setting while the API still queries v1:

```powershell
docker compose --env-file .env -f compose.yaml --project-name ekaa `
  exec -T -e EKAA_EMBEDDING_MODEL=deterministic-hash-v2 api `
  python -m backend.app.embeddings.cli embed-pending
```

Then compare the completed-chunk count with the count of distinct chunks having a
v2/64-dimensional row. Switch the API's `EKAA_EMBEDDING_MODEL` to v2 and recreate
only the API after that coverage is complete. Keep v1 rows for rollback. Never change
the API query model to v2 before all completed chunks have v2 rows. The example
command's Compose project/env file must be the exact existing database deployment;
the isolated Stage 21 project must not be pointed at it.

The RAG result may vary in wording, latency, or fail closed if the selected local
model does not follow the structured-answer contract. A refusal or provider error is
not a successful end-to-end answer. Check the reported safe public error and selected
model compatibility before deliberately rerunning the demo; the script does not
retry generation automatically. This is a local functional demonstration, not a
quality, throughput, production security, or SLO benchmark.

## Stop and retain or remove demo data

To stop the named Stage 21 services while retaining the database volume:

```powershell
docker compose --env-file .env.stage21 -f compose.yaml -f compose.stage21.yaml `
  --project-name ekaa-stage21-demo stop
```

To remove only the named project's containers and network, keeping its database
volume:

```powershell
docker compose --env-file .env.stage21 -f compose.yaml -f compose.stage21.yaml `
  --project-name ekaa-stage21-demo down
```

**Data-loss warning:** adding `--volumes` to `down`, or manually removing the
project's `postgres_data` volume, permanently deletes all documents, chunks,
embeddings, and database state in that Stage 21 project. Inspect the exact project
and volume first. Do not run volume cleanup against the default `ekaa` project or an
unverified project name. The PowerShell demo script never performs this cleanup.

## Verification record

See [`STAGE_21_REPORT.md`](STAGE_21_REPORT.md) for the checks actually executed and
whether a live local end-to-end run passed. Static configuration checks alone do not
count as live RAG verification.
