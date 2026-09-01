import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError


def test_vector_migration_creates_versioned_embedding_storage(tmp_path: Path) -> None:
    database_path = tmp_path / "migration.db"
    environment = os.environ.copy()
    environment["EKAA_DATABASE_URL"] = f"sqlite:///{database_path.as_posix()}"

    completed = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=Path(__file__).parents[2],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    inspector = inspect(create_engine(environment["EKAA_DATABASE_URL"]))
    assert "chunk_embeddings" in inspector.get_table_names()
    columns = {column["name"] for column in inspector.get_columns("chunk_embeddings")}
    assert {"embedding", "embedding_model", "embedding_dimensions"} <= columns


def test_hybrid_migration_keeps_sqlite_as_an_honest_relational_only_backend(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "hybrid-migration.db"
    environment = os.environ.copy()
    environment["EKAA_DATABASE_URL"] = f"sqlite:///{database_path.as_posix()}"

    completed = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=Path(__file__).parents[2],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert (
        "document_chunks"
        in inspect(create_engine(environment["EKAA_DATABASE_URL"])).get_table_names()
    )


def test_authorization_migration_adds_acl_columns_and_classification_check(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "authorization-migration.db"
    environment = os.environ.copy()
    environment["EKAA_DATABASE_URL"] = f"sqlite:///{database_path.as_posix()}"
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=Path(__file__).parents[2],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    engine = create_engine(environment["EKAA_DATABASE_URL"])
    inspector = inspect(engine)
    columns = {column["name"] for column in inspector.get_columns("documents")}
    assert {"classification", "allowed_groups"} <= columns
    with engine.begin() as connection, pytest.raises(IntegrityError):
        connection.execute(
            text(
                "INSERT INTO documents "
                "(id, filename, mime_type, checksum, status, classification, "
                "allowed_groups, created_at, metadata_json) "
                "VALUES ('bad', 'bad.md', 'text/markdown', :checksum, "
                "'completed', 'unknown', '[]', CURRENT_TIMESTAMP, '{}')"
            ),
            {"checksum": "f" * 64},
        )
