from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_stage21_overlay_enables_only_the_local_api_demo_capabilities() -> None:
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    overlay = (ROOT / "compose.stage21.yaml").read_text(encoding="utf-8")
    example_env = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert '"127.0.0.1:${EKAA_API_PORT:-8000}:8000"' in compose
    assert "internal: true" in compose
    assert 'EKAA_EMBEDDING_ENABLED: "true"' in overlay
    assert "EKAA_EMBEDDING_MODEL: deterministic-hash-v2" in overlay
    assert "EKAA_EMBEDDING_MODEL=deterministic-hash-v1" in example_env
    assert 'EKAA_RAG_ENABLED: "true"' in overlay
    assert 'EKAA_RAG_DIRECT_EVIDENCE_PROFILE: "true"' in overlay
    assert "EKAA_LLM_MODEL: ${EKAA_LLM_MODEL:-qwen2.5:3b}" in overlay
    assert 'EKAA_RAG_MIN_EVIDENCE_SCORE: "0.2"' in overlay
    assert 'EKAA_LLM_TIMEOUT_SECONDS: "60"' in overlay
    assert 'EKAA_LLM_MAX_RETRIES: "0"' in overlay
    assert 'EKAA_RELIABILITY_RAG_DEADLINE_SECONDS: "90"' in overlay
    assert 'EKAA_LLM_ENABLED: "true"' in overlay
    assert 'EKAA_HYBRID_RETRIEVAL_ENABLED: "false"' in overlay
    assert 'EKAA_RERANKING_ENABLED: "false"' in overlay
    assert "ollama_bridge" in overlay
    assert "services:\n  db:" not in overlay
    assert "ports:" not in overlay


def test_stage21_demo_contract_is_synthetic_loopback_and_non_destructive() -> None:
    script = (ROOT / "scripts/stage21-local-demo.ps1").read_text(encoding="utf-8")
    fixture = (ROOT / "docs/fixtures/stage21_synthetic_recovery.md").read_text(
        encoding="utf-8"
    )
    assert "127.0.0.1" in script
    assert "The demo API host must be 127.0.0.1" in script
    assert "^ekaa-stage21-[a-z0-9]" in script
    assert "/api/documents" in script
    assert "/api/retrieval/search" in script
    assert "/api/rag/answer" in script
    assert "backend.app.embeddings.cli" in script
    assert "'exec', '-T', 'api'" in script
    assert "'run', '--rm'" not in script
    assert "deterministic-hash-v2" in script
    assert "supported" in script
    assert "document_id -eq $upload.document_id" in script
    assert "$_.score -ge 0.2" in script
    assert "docker compose down" not in script
    assert "volume rm" not in script
    assert "synthetic demo recovery target is 47 minutes" in fixture
    assert "fictional data" in fixture


def test_stage21_local_credentials_are_ignored_and_template_is_local_only() -> None:
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    template = (ROOT / ".env.stage21.example").read_text(encoding="utf-8")
    script = (ROOT / "scripts/stage21-local-demo.ps1").read_text(encoding="utf-8")
    assert ".env.stage21" in gitignore
    assert "stage21-local-only-change-me" in template
    assert "host.docker.internal:11434/v1" in template
    assert "qwen2.5:3b" in template
    assert "[string]$Model = 'qwen2.5:3b'" in script
