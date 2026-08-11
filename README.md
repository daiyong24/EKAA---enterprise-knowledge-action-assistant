# Enterprise Knowledge & Action Assistant (EKAA)

EKAA is a stage-built, production-style reference project for an authorized,
grounded enterprise knowledge assistant and, later, a human-approved action agent.

## Current Status

Stage 1 — LLM Foundation.

Implemented now: FastAPI foundation plus a provider-neutral, stateless LLM chat
boundary. LLM is disabled unless explicitly configured. RAG, database, frontend,
authentication, tools, agents, and chat memory are intentionally absent.

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

To enable a compatible provider locally, set `EKAA_LLM_ENABLED=true`,
`EKAA_LLM_API_KEY`, and `EKAA_LLM_MODEL` in your untracked `.env`. Then send a
`POST /api/chat` body such as `{"message":"Explain RAG."}`. Stage 1 accepts only
general conceptual questions and intentionally withholds free-text provider output
until grounded generation exists; tests never make provider requests.

## Quality Checks

```powershell
pytest
ruff check .
ruff format --check .
mypy backend
```

See `AGENTS.md` before making changes, `ROADMAP.md` for stage gates, and
`docs/PLAN_REVIEW.md` for the review of the original starting plan.
