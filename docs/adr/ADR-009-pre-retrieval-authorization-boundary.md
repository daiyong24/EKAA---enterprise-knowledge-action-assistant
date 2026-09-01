# ADR-009: Pre-retrieval authorization boundary

## Context

Stage 6 retrieval has semantic and PostgreSQL full-text branches. Stage 9 must make
it impossible for a protected chunk to reach either branch before authorization.
The application must remain testable without selecting a production IAM vendor.

## Decision

Store document classification and group ACL metadata alongside document metadata.
Resolve a principal through a replaceable `IdentityProvider`, then use an
`AuthorizedRetrievalService` adapter to query only authorized document IDs and pass
that set to both retrieval branches. Use a request-scoped context variable to keep
the existing RAG service contract and isolate concurrent requests. Fail closed if
identity or ACL metadata cannot be established. Record sanitized authorization
events through a replaceable audit sink.

## Consequences

The vector and keyword repositories retain their existing ranking semantics while
receiving a mandatory, server-owned filter. Anonymous access remains possible only
for explicitly public documents. A local static-token provider is available for
tests and demos; production identity verification remains an integration task.
