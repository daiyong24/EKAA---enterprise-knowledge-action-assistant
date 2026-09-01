import asyncio
from collections.abc import Sequence
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from backend.app.authorization.audit import InMemoryAuditSink
from backend.app.authorization.errors import (
    AuthorizationUnavailableError,
    InvalidIdentityError,
)
from backend.app.authorization.identity import StaticTokenIdentityProvider
from backend.app.authorization.models import ANONYMOUS, Principal
from backend.app.authorization.service import (
    AuthorizationService,
    AuthorizedRetrievalService,
    PrincipalContext,
)
from backend.app.db.models import Base, Document
from backend.app.embeddings.models import RetrievedChunk
from backend.app.embeddings.repository import (
    EmbeddingRepository,
    LexicalRetrievalRepository,
)


def _session_factory(tmp_path: Path) -> sessionmaker[Session]:
    engine = create_engine(f"sqlite:///{(tmp_path / 'auth.db').as_posix()}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory.begin() as session:
        session.add_all(
            [
                Document(
                    id="public-doc",
                    filename="public.md",
                    mime_type="text/markdown",
                    checksum="1" * 64,
                    status="completed",
                    classification="public",
                    allowed_groups=[],
                ),
                Document(
                    id="internal-doc",
                    filename="internal.md",
                    mime_type="text/markdown",
                    checksum="2" * 64,
                    status="completed",
                    classification="internal",
                    allowed_groups=[],
                ),
                Document(
                    id="finance-doc",
                    filename="finance.md",
                    mime_type="text/markdown",
                    checksum="3" * 64,
                    status="completed",
                    classification="confidential",
                    allowed_groups=["finance"],
                ),
            ]
        )
    return factory


def test_positive_negative_role_group_matrix(tmp_path: Path) -> None:
    service = AuthorizationService(audit_sink=InMemoryAuditSink())
    factory = _session_factory(tmp_path)
    with factory() as session:
        columns = {
            column["name"] for column in inspect(session.bind).get_columns("documents")
        }
        assert {"classification", "allowed_groups"} <= columns
        assert service.authorized_document_ids(session) == ("public-doc",)
        assert service.authorized_document_ids(
            session, principal=Principal("employee", groups=frozenset())
        ) == ("internal-doc", "public-doc")
        assert service.authorized_document_ids(
            session, principal=Principal("finance-user", groups=frozenset({"finance"}))
        ) == ("finance-doc", "internal-doc", "public-doc")
        assert service.authorized_document_ids(
            session, principal=Principal("admin", roles=frozenset({"admin"}))
        ) == ("finance-doc", "internal-doc", "public-doc")
        session.add(
            Document(
                id="invalid-doc",
                filename="invalid.md",
                mime_type="text/markdown",
                checksum="4" * 64,
                status="completed",
                classification="unknown",
            )
        )
        with pytest.raises(IntegrityError):
            session.flush()


def test_unknown_classification_and_malformed_principals_fail_closed() -> None:
    service = AuthorizationService(audit_sink=InMemoryAuditSink())
    assert not service.can_read(
        Principal("admin", roles=frozenset({"admin"})),
        classification="future-classification",
        allowed_groups=(),
    )
    with pytest.raises(ValueError):
        Principal("")
    with pytest.raises(ValueError):
        Principal("x" * 129)
    with pytest.raises(ValueError):
        Principal("user", groups=frozenset({"group with spaces"}))


def test_vector_and_keyword_queries_require_the_same_server_owned_acl_filter() -> None:
    vector_statement = EmbeddingRepository().search_statement(
        query_vector=[1.0, 0.0],
        model="model",
        dimensions=2,
        top_k=5,
        allowed_document_ids=["authorized-doc"],
    )
    lexical_statement = LexicalRetrievalRepository().search_statement(
        query="secret",
        exact_identifiers=(),
        top_k=5,
        allowed_document_ids=["authorized-doc"],
    )
    vector_compiled = vector_statement.compile(dialect=postgresql.dialect())
    lexical_compiled = lexical_statement.compile(dialect=postgresql.dialect())
    assert "documents.id IN" in str(vector_compiled)
    assert "documents.id IN" in str(lexical_compiled)
    assert "authorized-doc" in str(vector_compiled.params)
    assert "authorized-doc" in str(lexical_compiled.params)
    assert "unauthorized-doc" not in str(vector_compiled.params)
    assert "unauthorized-doc" not in str(lexical_compiled.params)


class CapturingRetrieval:
    def __init__(self) -> None:
        self.allowed: list[Sequence[str] | None] = []

    def search(
        self,
        *,
        query: str,
        top_k: int,
        allowed_document_ids: Sequence[str] | None = None,
    ) -> list[RetrievedChunk]:
        del query, top_k
        self.allowed.append(allowed_document_ids)
        return []


def test_authorized_adapter_filters_before_vector_or_keyword_delegate(
    tmp_path: Path,
) -> None:
    factory = _session_factory(tmp_path)
    sink = InMemoryAuditSink()
    delegate = CapturingRetrieval()
    adapter = AuthorizedRetrievalService(
        retrieval_service=delegate,
        authorization_service=AuthorizationService(audit_sink=sink),
        session_factory=factory,
    )
    with PrincipalContext(Principal("employee")):
        adapter.search(query="secret", top_k=5)
    assert delegate.allowed == [("internal-doc", "public-doc")]
    assert all(event.action == "retrieval_authorization" for event in sink.snapshot())


def test_identity_error_and_metadata_error_fail_closed(tmp_path: Path) -> None:
    provider = StaticTokenIdentityProvider({"valid": Principal("user")})
    with pytest.raises(InvalidIdentityError):
        provider.resolve({"authorization": "Bearer invalid"})
    sink = InMemoryAuditSink()
    service = AuthorizationService(audit_sink=sink)

    def broken_factory() -> Session:
        engine = create_engine("sqlite:///:memory:")
        return Session(engine)

    adapter = AuthorizedRetrievalService(
        retrieval_service=CapturingRetrieval(),
        authorization_service=service,
        session_factory=broken_factory,
    )
    with PrincipalContext(ANONYMOUS), pytest.raises(AuthorizationUnavailableError):
        adapter.search(query="anything", top_k=1)
    assert sink.snapshot()[-1].outcome == "error"


def test_authorization_is_not_cached_and_contexts_are_isolated(tmp_path: Path) -> None:
    factory = _session_factory(tmp_path)
    sink = InMemoryAuditSink()
    delegate = CapturingRetrieval()
    adapter = AuthorizedRetrievalService(
        retrieval_service=delegate,
        authorization_service=AuthorizationService(audit_sink=sink),
        session_factory=factory,
    )

    async def call(principal: Principal) -> None:
        with PrincipalContext(principal):
            await asyncio.sleep(0)
            adapter.search(query="q", top_k=1)

    async def run() -> None:
        await asyncio.gather(
            call(Principal("employee")),
            call(Principal("finance", groups=frozenset({"finance"}))),
        )

    asyncio.run(run())
    assert delegate.allowed.count(("internal-doc", "public-doc")) == 1
    assert delegate.allowed.count(("finance-doc", "internal-doc", "public-doc")) == 1
    with PrincipalContext(ANONYMOUS):
        adapter.search(query="q", top_k=1)
    assert delegate.allowed[-1] == ("public-doc",)
