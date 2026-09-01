# Stage 9 — Authentication & Authorization Design

Stage 9 places identity and authorization before every protected retrieval path.
The implementation is provider-neutral: `IdentityProvider` is the replaceable
boundary, while local development uses anonymous access or injected static tokens.
No production IAM SDK or credential is stored in the repository.

## Request flow

```text
request -> identity provider -> PrincipalContext -> ACL ID resolution
         -> vector and keyword retrieval (authorized IDs only)
         -> rerank/context/generation -> response
```

`AuthorizedRetrievalService` resolves document IDs before delegating to semantic or
lexical retrieval. An empty authorized set remains an empty SQL `IN` predicate; it
never means “no filter”. Concurrent requests use a `ContextVar` so identities cannot
bleed across requests.

## Policy

- `public`: readable anonymously.
- `internal`: readable by any authenticated principal.
- `confidential` and `restricted`: readable by an administrator or a principal in
  at least one document `allowed_groups` entry.
- `admin` and `knowledge_admin` roles can update document access metadata and read
  all classifications through the same pre-retrieval filter.
- Missing, malformed, or unavailable identity/ACL metadata fails closed with a safe
  public error.

Documents default to `public` for the existing synthetic fixture. A minimal admin
endpoint can change classification and groups without returning document content.

## Audit and privacy

The local `InMemoryAuditSink` records action, actor, outcome, timestamp, correlation
ID, resource ID where needed, and a bounded reason code. It does not record query
text, document content, credentials, or provider payloads. Production deployments
must replace this sink with a durable restricted audit implementation.

## Explicit non-goals

This stage does not claim full enterprise IAM parity, multi-tenancy, production
credential verification, or protected enterprise data. Those require an explicitly
selected integration and additional security review.
