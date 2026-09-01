# Stage 8 local Ollama baseline timeout fix

## Metadata

- Status: HIGH-risk replanning after independent reviews
- Risk: HIGH — the local-provider egress boundary can send synthetic corpus-derived
  context outside the documented endpoint through hostname resolution or proxy
  environment configuration. This escalated from MEDIUM after independent review.
- Baseline: `6473ff5`; existing Stage 8 worktree changes are uncommitted and preserved.

## Original issue and reproduction

The approved synthetic-only Stage 8 `qwen3:4b` Ollama generation replay did not
complete before the controlled 15-minute command limit and wrote no output artifact.
The runner was stopped by targeting only the confirmed command child processes.

Confirmed evidence:

- The local Ollama API reports `qwen3:4b`; a simple compatibility request succeeds.
- Stage 8 preflight verifies the corpus SHA equals the reviewed policy SHA.
- A bounded OpenAI-compatible diagnostic request with `think=false` and
  `max_tokens=128` completed in 4.841 seconds but used all 128 completion tokens
  and produced no final content.
- No baseline JSON or CSV artifact was created.

## Evidence and root-cause review

- ROOT_CAUSE_VERIFIED: the Stage 8 local target constructs the generic
  `OpenAICompatibleProvider`, whose request body contains only `model` and
  `messages`. It cannot send the local Ollama `/v1` controls needed by Qwen3 for
  this structured evaluation: `reasoning_effort: "none"`, JSON-object response
  mode, and an explicit output bound.
- CONFIRMED contributing factors: the current 15-second provider timeout with two
  retries can spend roughly 45 seconds on one serial question; JSON/CSV are emitted
  only after all 50 questions complete, so an interrupted run has no artifact.
- DISPROVEN: local Ollama/model availability, approved corpus/policy pin failure,
  and malformed citation contract as the sole cause. Bounded synthetic probes using
  the documented compatible controls completed with non-empty, parser-valid final
  JSON. The original `think=false` diagnosis is not a reliable `/v1` control.

## Intended behavior and preserved boundaries

The explicit local evaluation target must complete bounded synthetic-only requests
or record safe failures. It must keep the exact loopback endpoint, approved corpus
path plus SHA pin, no database/public endpoint/private content access, no artifact
content leakage, provider-neutral production boundary, and existing RAG/citation
validation semantics.

## Verified minimal implementation plan

### Change and rationale

1. Preserve `OpenAICompatibleProvider`'s exact default body (`model` plus ordered
   system/user messages), while adding a protected internal body-extension hook.
2. Add a private adapter in `evaluation/local_generation.py`, selected only by the
   explicit synthetic, loopback-only Ollama target. It adds exactly:
   - `reasoning_effort: "none"`;
   - `response_format: {"type": "json_object"}`; and
   - `max_tokens: 256`.
   The 256-token limit is a conservative bound for this reviewed corpus's concise
   one-fact-per-question citation proposal; a cap-hit remains a safe malformed-output
   failure, never a partial answer/citation.
3. Create an evaluation-only effective settings profile: `httpx.Timeout(10.0)` sets
   independent 10-second pool/connect/read/write phase timeouts and `RagService`
   retries are zero. It records safe failures for timeout/transport errors but does
   not claim a hard request, per-question, or total replay duration.
   Do not change the configured/general provider timeout or retry policy.
4. Include local profile values (`reasoning_effort`, response mode, max tokens,
   HTTPX phase timeout, effective retries) in the non-secret configuration hash and
   increment the local target version to make previous/future artifacts
   non-comparable when controls differ.

### In scope

- `backend/app/llm/provider.py` internal request-body extension point only.
- `backend/app/llm/models.py` compatible optional internal `ChatResult.finish_reason`.
- `backend/app/llm/errors.py` optional normalized usage on typed provider-output errors.
- `backend/app/evaluation/local_generation.py` private local adapter and effective
  profile only.
- `backend/app/rag/errors.py` optional normalized usage on typed RAG-output errors.
- `backend/app/rag/service.py` content-free usage propagation through provider-output
  and citation-parser rejection.
- provider/evaluation unit tests; Stage 8 design/report; this plan.
- `tests/unit/test_openai_compatible_provider.py` and
  `tests/unit/test_rag_service.py` plus evaluation tests.

### Explicitly out of scope

No public API/CLI/settings contract, generic provider behavior, RAG/citation parser,
retrieval/reranking, database/migration, authorization, corpus/dataset/policy data,
or committed artifacts change. New measured artifacts stay under `C:\\safe-local`.

The compatible metadata/error changes are internal only: `finish_reason` defaults
when a provider omits it; typed errors carry only validated numeric usage; response
mapping parses usage before later output-shape rejection; and `RagService` passes
that usage into its own fixed, content-free output error. No provider text, payload,
or exception detail enters public responses or evaluation artifacts.

### Tests and verification

- Mock generic request is exactly unchanged; mock local request contains exactly the
  three fixed local controls in addition to the generic body.
- Valid structured response is accepted by existing citation parsing/validation;
  malformed or cap-truncated output, timeout, and retryable errors yield safe,
  content-free failure observations. The local profile makes one provider attempt;
  ordinary RAG retry behavior remains covered and unchanged.
- Existing loopback/path/SHA, no-evidence/no-provider, fake injection, artifact
  exclusion, and no-secret provenance tests remain required.
- Run targeted tests, full pytest, Ruff, format, mypy, and `git diff --check`.
- Run one real approved-corpus question using the actual target, then the fresh
  50-question replay. Verify 50 records, corpus/policy/config provenance, bounded
  failure behavior, and no content/credentials in artifacts. A measured replay is
  still not a correctness/faithfulness/citation-quality baseline until calibrated
  human review is provided.

### Rollback and residual risks

Revert the internal hook and local adapter together; normal provider behavior is
unchanged. The 256-token cap may produce safe failure rows if a model ignores the
concise contract. The local controls are verified only for this installed Ollama/Qwen3
combination; other versions require a new measured run.

## Implementation record

Implemented changes:

- Added the protected internal request-body extension hook to the generic provider;
  the default request body remains exactly model plus ordered system/user messages.
- Added the private Stage 8 local Ollama adapter/profile with `reasoning_effort=none`,
  JSON-object response mode, `max_tokens=256`, 10-second HTTPX phase timeouts,
  and zero `RagService` retries.
- Bumped the local target to `1.1.0` and included every effective profile control in
  the non-secret configuration fingerprint.
- Added exact mock request tests, profile-fingerprint coverage, and safe valid,
  malformed/cap-truncated, timeout, and retryable-error evaluation cases.

Verification evidence (implementation pass):

- PASS — focused provider/evaluation tests: `41 passed`.
- PASS — focused Ruff lint and format check.
- PASS — focused strict mypy for the changed source modules.
- PASS — full repository pytest: `179 passed`, `4 skipped` (the skips require the
  explicitly configured disposable pgvector test database).
- PASS — repository Ruff lint, format check, strict mypy, and `git diff --check`.
- NOT_RUN — real Ollama request and 50-question replay, by implementation scope.
- Pending at this record: independent review.

## High-risk review findings and replan requirements

The first implementation is not accepted. New independent evidence requires return
to planning before any real replay:

- ROOT_CAUSE_VERIFIED (security): accepting `localhost` delegates the local-only
  guarantee to mutable DNS/hosts resolution. The current client also uses HTTPX's
  default `trust_env=True`, so an environment proxy can route even literal
  `127.0.0.1` externally.
- MAJOR (code): syntactically valid JSON terminated at the 256-token cap can be
  accepted because the completion reason is discarded.
- MAJOR (code): usage from a provider response is lost when citation parsing rejects
  it, causing false zero-token/cost accounting for failures.
- MINOR (code): HTTPX phase timeouts are not a true request/per-question wall-clock
  deadline; documentation must not claim a complete-run wall-clock bound.

The revised HIGH plan must restrict the endpoint to exactly
`http://127.0.0.1:11434/v1`, set `trust_env=False` only for the private local
evaluation adapter, preserve/reject cap completion reasons, preserve safe usage in
malformed-output failure observations, and accurately document the HTTPX phase-timeout
behavior without a false complete-run deadline.
It must complete security, architecture, business, code, and final verification.

### Revised HIGH implementation plan

1. Keep the generic request/client defaults unchanged. The private Stage 8 adapter
   must accept only the normalized exact URL `http://127.0.0.1:11434/v1`, override
   client construction with `trust_env=False`, and replace any configured API key
   with the fixed non-secret `ollama-local` marker before a client/header exists.
   Tests must prove generic header/environment behavior is unchanged and the local
   request never contains a configured real key.
2. Carry an optional internal `finish_reason` on normalized provider results and
   optional normalized numeric usage on provider/RAG output exceptions. The private
   adapter permits only `stop`; `length`, missing, or unknown completion reasons are
   content-free output failures even if JSON parses. Citation-parser failures retain
   only usage and deterministic retrieval IDs, never model text/claims/citations.
3. Do not add a false strict wall-clock deadline. The private adapter instead keeps
   `httpx.Timeout(10.0)` pool/connect/read/write phase timeouts and zero RAG retries; a timeout or
   retryable transport error remains one existing content-free failure row. The
   design/report will explicitly state that serial total duration is not a hard SLO:
   HTTP phase timeouts and event-loop shutdown cannot honestly guarantee a bound for
   arbitrary cancellation-suppressing code. The actual runtime target has no
   arbitrary injected provider path; injection is test-only.
4. Include canonical endpoint identity, marker-key mode, proxy isolation, finish
   policy, max tokens, HTTPX phase timeout, and retries in the non-secret configuration
   fingerprint; bump local target version to `1.2.0`.
5. Required tests add endpoint variant rejections; client proxy isolation; marker
   header replacement; generic behavior preservation; valid-JSON `length` rejection;
   usage propagation through malformed/citation failure rows; retrieval-ID
   preservation; failure artifact exclusion; and
   ordinary provider timeout/one-attempt behavior. Then run all static checks, a
   real one-question smoke, full replay,
   and HIGH code/architecture/business/security/concurrency/final reviews.

## Smoke-command refinement

Final verification confirmed that the required live one-question smoke was not
reproducibly executable because the CLI always evaluated all 50 questions. The
minimal HIGH-risk refinement is a `--question-id` selector accepted only together
with `--synthetic-ollama-corpus` and `--allow-local-ollama`. It must load and validate
the complete reviewed dataset and full pinned corpus before selecting exactly one
known question, reject unknown/empty IDs and assessments before target construction,
and persist `single_question_smoke` evaluation scope in the content-free summary.
Smoke scope must be rejected by regression comparison; full commands retain
`full_dataset` scope and existing 50-question behavior. Required implementation scope
is evaluation CLI/models/runner/regression, tests, README/design, and this plan only;
no provider/RAG/security boundary or data change.

## Final verification and outcome

- Status: COMPLETE — the local Ollama timeout/egress/output-accounting/smoke-command
  defect sequence is resolved and independently verified.
- Automated checks: `192 passed, 4 skipped`; Ruff, formatting, strict mypy, and
  `git diff --check` passed. The four skips require an explicitly configured
  disposable pgvector database and are unrelated to this target.
- Live verification: one constrained smoke and one 50-question full synthetic replay
  completed through local `qwen3:4b` target `1.2.0`; artifacts remained content-free
  and matched the report. Full outcome: 50 records, 47 non-failure rows, three coarse
  safe failures, and 40 unsupported claims among 47 emitted claims.
- Decision: the repair is complete, but the measured model result is NOT_PROMOTED as
  a Stage 8 quality baseline. The validator was not weakened. Calibrated human
  correctness, faithfulness, and citation-accuracy assessment, plus a price schedule
  if cost is required, remain future work.

## Revised implementation ledger

- IMPLEMENTED — the private local adapter now accepts only the exact canonical
  `http://127.0.0.1:11434/v1` setting, constructs its client with
  `trust_env=False`, and substitutes the fixed non-secret `ollama-local` marker key
  before header/client construction. Generic provider defaults remain unchanged.
- IMPLEMENTED — provider results retain optional internal completion reason and
  typed output errors retain only normalized numeric usage. The local adapter accepts
  only `stop`; every other/missing reason becomes a content-free output error.
- IMPLEMENTED — RAG passes normalized usage through provider-output and
  citation-parser output errors. The local evaluation target records those safe
  failures with deterministic retrieval IDs and no model content.
- IMPLEMENTED — local target version is `1.2.0`; its non-secret fingerprint now
  includes canonical endpoint identity, marker-key mode, proxy isolation, finish
  policy, and all effective controls.
- PASS — focused provider/RAG/evaluation tests: `61 passed`.
- PASS — full repository pytest: `187 passed`, `4 skipped` (the skips require an
  explicitly configured disposable pgvector test database).
- PASS — repository Ruff lint, formatter check, strict mypy, and
  `git diff --check`.
- NOT_RUN — real one-question Ollama smoke and 50-question replay, as required by
  the implementer scope and pending independent HIGH-risk review.
- Plan deviations: none. The initial focused test run exposed only expected obsolete
  pre-replan assertions (version `1.1.0`, `localhost`, and absent failure usage);
  they were updated and the focused suite was rerun successfully.

## Smoke-command implementation record

- IMPLEMENTED — Added `--question-id` only for the explicit synthetic Ollama target
  with its local opt-in. The command validates the complete reviewed dataset and
  pinned corpus before selecting one known question and before local target
  construction.
- IMPLEMENTED — Smoke runs reject assessment input and serialize
  `single_question_smoke`; normal commands serialize `full_dataset`. Regression
  comparison rejects smoke scopes.
- Pending at this record: focused/full checks and independent HIGH-risk review.
