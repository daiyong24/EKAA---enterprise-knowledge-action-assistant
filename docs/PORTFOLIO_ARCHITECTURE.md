# EKAA Architecture and System Diagrams

## System boundary

```mermaid
flowchart LR
    U[Reviewer or client] --> API[FastAPI API]
    API --> AUTH[Identity and pre-retrieval authorization]
    AUTH --> RET[RAG retrieval]
    RET --> CTX[Bounded context and citation validation]
    CTX --> LLM[Provider-neutral LLM boundary]
    LLM --> RESP[Safe answer or insufficient evidence]
    API --> AG[Single-agent baseline]
    AG --> AP[Scoped approval service]
    AP --> EXT[Injected external executor]
    EXT --> AUD[Audit and telemetry]
    API --> OBS[Metrics and tracing]
    RET --> EVAL[Versioned evaluation runner]
    AG --> EVAL
    EVAL --> ART[Content-minimized run artifact]
```

The arrows express dependency direction, not a claim that every edge is enabled in
the default local process. Authorization is applied before protected retrieval;
retrieved text, prompts, and tool output remain untrusted data. External writes are
impossible without scoped human approval and are not exercised by the portfolio demo.

## Evaluation/release evidence flow

```mermaid
sequenceDiagram
    participant D as Versioned dataset/corpus
    participant R as Local runner
    participant A as Run artifact
    participant V as Portfolio validator
    participant Reviewer as Reviewer
    D->>R: Load synthetic fixtures and policy
    R->>A: Write content-minimized metrics + provenance
    A->>V: Verify SHA-256 and run identity
    V->>Reviewer: Report reproducible commands and limitations
```

## Runtime safety boundaries

```mermaid
flowchart TD
    Input[Untrusted input] --> Validate[Validate size, path, URL and schema]
    Validate --> Authorize[Authorize before retrieval]
    Authorize --> Retrieve[Retrieve bounded evidence]
    Retrieve --> Ground[Ground and validate citations]
    Ground --> Answer[Return answer or safe refusal]
    Answer --> Observe[Redacted telemetry]
    Proposal[Write proposal] --> Approval[Exact action/parameter approval]
    Approval --> Execute[Idempotent injected executor]
    Execute --> Audit[Sanitized audit result]
```

