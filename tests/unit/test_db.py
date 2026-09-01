from backend.app.db.session import create_database_engine


def test_postgresql_engine_has_bounded_connect_and_pool_waits() -> None:
    engine = create_database_engine(
        "postgresql+psycopg://user:pass@localhost/example",
        connect_timeout_seconds=7,
        pool_timeout_seconds=3.5,
    )

    assert engine.pool._timeout == 3.5  # type: ignore[attr-defined]
    engine.dispose()


def test_sqlite_engine_does_not_receive_postgresql_pool_options() -> None:
    engine = create_database_engine("sqlite:///:memory:")

    assert engine.url.get_backend_name() == "sqlite"
    engine.dispose()
