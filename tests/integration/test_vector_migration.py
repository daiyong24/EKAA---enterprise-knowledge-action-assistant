import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect


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
