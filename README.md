# Enterprise Knowledge & Action Assistant (EKAA)

EKAA is a stage-built, production-style reference project for an authorized,
grounded enterprise knowledge assistant and, later, a human-approved action agent.

## Current Status

Stage 2 — Document Ingestion.

Implemented now: FastAPI foundation, a provider-neutral stateless LLM boundary,
and safe ingestion of synthetic/public PDF, UTF-8 TXT, and UTF-8 Markdown into
normalized relational chunks. LLM is disabled unless explicitly configured. RAG,
embeddings, retrieval, authentication, frontend, tools, agents, and chat memory
are intentionally absent.

## Requirements

- Python 3.12+

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

## Quality Checks

```powershell
pytest
ruff check .
ruff format --check .
mypy backend
```

See `AGENTS.md` before making changes, `ROADMAP.md` for stage gates, and
`docs/PLAN_REVIEW.md` for the review of the original starting plan.
