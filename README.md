# Enterprise Knowledge & Action Assistant (EKAA)

EKAA is a stage-built, production-style reference project for an authorized,
grounded enterprise knowledge assistant and, later, a human-approved action agent.

## Current Status

Stage 0 — Project Foundation.

Implemented now: FastAPI foundation, settings, logging, exception mapping, health
endpoint, tests, and quality automation. LLM, RAG, database, frontend, and agent
features are intentionally absent.

## Requirements

- Python 3.12+

## Local Setup

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
uvicorn backend.app.main:app --reload
```

Open `http://127.0.0.1:8000/health`. Expected response:

```json
{"status":"ok"}
```

## Quality Checks

```powershell
pytest
ruff check .
ruff format --check .
mypy backend
```

See `AGENTS.md` before making changes, `ROADMAP.md` for stage gates, and
`docs/PLAN_REVIEW.md` for the review of the original starting plan.
