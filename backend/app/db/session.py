"""SQLAlchemy engine and session-factory construction."""

from collections.abc import Callable

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

SessionFactory = Callable[[], Session]


def create_database_engine(
    database_url: str,
    *,
    connect_timeout_seconds: int = 5,
    pool_timeout_seconds: float = 5.0,
) -> Engine:
    """Construct an engine with bounded PostgreSQL connect/pool waits.

    SQLite's local pool implementations do not accept QueuePool's timeout
    argument, so those options are applied only to PostgreSQL URLs.
    """
    connect_args: dict[str, object] = {}
    if database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    elif database_url.startswith("postgresql"):
        connect_args["connect_timeout"] = connect_timeout_seconds
    engine_options: dict[str, object] = {
        "pool_pre_ping": True,
        "connect_args": connect_args,
    }
    if database_url.startswith("postgresql"):
        engine_options["pool_timeout"] = pool_timeout_seconds
    return create_engine(database_url, **engine_options)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
