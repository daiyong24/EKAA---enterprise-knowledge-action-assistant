# Stage 1 Provider Decision Record

## Decision

Stage 1 uses a small `OpenAICompatibleProvider` adapter that calls the standard
chat-completions HTTP shape through `httpx`. The application depends only on its
`LLMProvider` protocol and normalized `ChatResult`, not on a provider SDK or a
vendor response type.

The adapter is selected by `EKAA_LLM_PROVIDER=openai_compatible`. It is disabled
by default. Enabling it requires an environment-only API key and a separately
configured model name; neither is hard-coded or committed.

## Why this boundary

- An HTTP adapter is easy to replace or fake in tests and avoids binding the API
  contract to an SDK.
- The normalized response requires answer text, provider-returned model name, and
  consistent input/output/total token usage.
- Timeouts and retries are bounded by settings. Only timeouts, transport failures,
  408, 429, and 5xx responses retry. Other 4xx responses and malformed provider
  output fail without retries.

## Cost assumptions

No provider, model, price, or budget is selected in source code. Cost varies with
the chosen compatible provider and model. The API exposes provider-reported token
usage so a later stage can calculate costs from a versioned price schedule; Stage 1
does not claim cost estimates or enforce a budget.

## Privacy assumptions and limits

An enabled provider receives only the Stage 1 system prompt and the current user
message. The service does not persist chat content, use chat memory, retrieve
documents, or log message text, response text, API keys, or provider payloads.

## Stage 1 enterprise-evidence response policy

Stage 1 cannot answer organization-specific questions because it has no authorized
knowledge base. Therefore `/api/chat` is deliberately narrowed to clearly general,
conceptual explanations (for example, "Explain RAG" or "How does RAG work?"). A
deterministic application-layer policy rejects every other request—including entity
lookups, common factual-lookup wording, status/policy questions, and explicit
organization references—before a provider is called. This is conservative by design:
some concept phrasing, such as "Explain what RAG is", is rejected rather than risk a
fact lookup. For an allowed conceptual request, the provider call is made and its
model/usage are returned, but its free-text content is withheld.

This conservative policy is an enforceable Stage 1 product boundary. No finite
classifier can prove arbitrary provider text lacks an unsupported enterprise factual
claim, so Stage 1 deliberately exposes no free-text provider output. It trades a
user-facing answer for groundedness until Stage 4 retrieval and Stage 9
authorization provide evidence and access control. Requests outside the
conceptual-explanation scope receive a safe refusal.

Provider retention, training/data-use terms, DPA, region/data residency, and
enterprise-data approval are **UNKNOWN** until a provider and deployment region are
formally selected. Therefore Stage 1 is limited to synthetic or explicitly public
test content as required by BR-004 and BR-009; it must not receive enterprise
confidential data.

## Operational configuration

Use an untracked `.env` or deployment secret store:

```text
EKAA_LLM_ENABLED=true
EKAA_LLM_PROVIDER=openai_compatible
EKAA_LLM_BASE_URL=https://provider.example/v1
EKAA_LLM_API_KEY=<secret>
EKAA_LLM_MODEL=<provider-model-name>
```

Production configuration rejects a non-HTTPS base URL. Tests use in-process fakes
or `httpx.MockTransport` and make no network or paid provider calls.
