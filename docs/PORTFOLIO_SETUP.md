# Portfolio Setup and Reproduction

## Fresh-machine setup

Requirements for the documented path: Windows PowerShell, Python 3.12+, and Git.
Docker is optional for the portfolio replay and is not needed for the deterministic
checks. POSIX shell commands are not currently maintained in this guide; use the
equivalent Python commands from a POSIX environment only after adapting activation
and path syntax.

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
python scripts/stage20_portfolio.py validate
python scripts/stage20_portfolio.py demo --output-dir .stage20-demo-output
python scripts/stage20_portfolio.py audit-metrics
```

The demo reads only the checked-in synthetic dataset, policy, corpus, and retrieval
artifact. It does not open PostgreSQL, contact Ollama, call an LLM, write to an API,
or make external changes. The output directory is disposable and ignored by Git.

## Full engineering checks

```powershell
pytest -q
ruff check .
ruff format --check .
mypy backend
git diff --check
```

## Optional Docker smoke path

Docker Compose is documented in `docs/deployment/STAGE_17_DEPLOYMENT.md`. It is an
environment-dependent deployment contract, not required for the Stage 20 demo. Do
not add credentials to `.env`, image layers, or command history.

## Expected demo output

The command prints a small JSON summary containing the fixture IDs, run ID, and
artifact digest. It intentionally omits questions, answers, document text, and
secrets. A successful replay proves that the checked-in artifact and manifest are
internally consistent; it is not a new quality benchmark.
