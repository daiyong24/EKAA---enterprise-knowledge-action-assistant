# Stage 19 — Multi-Agent Architecture Design

## Hypothesis and decision rule

The Stage 14 bounded single-agent workflow is the control baseline. The hypothesis
for this candidate is that a small, sequential specialist route (researcher →
verifier → synthesizer) may improve tool/argument correctness on tasks that need
independent verification. Multi-agent is adopted only if the complete, matched
Stage 14 scenario suite shows a declared success improvement while staying within
the declared latency, step, tool/argument-accuracy, escalation-correctness, and
approval-compliance gates. A single successful
example is not sufficient evidence.

The reference experiment deliberately retains single-agent as the default. Stage 14
has a 3/3 deterministic contract baseline, so the candidate must demonstrate a
meaningful improvement rather than merely adding workers.
Adoption also requires at least three matched replicate runs; a one-off replay is
insufficient statistical evidence. Three replicates establish reproducibility only,
not statistical significance; a typed, authoritative and independently verified
powered-analysis artifact is required before adoption. Without that artifact,
comparison returns `statistical_evidence_unavailable` and retains single-agent.

## Candidate architecture

`CandidateRouter` produces either `single-agent` or the bounded specialized route.
`MultiAgentOrchestrator` invokes provider-neutral worker protocols sequentially. It
does not call tools, write to external systems, or contain vendor SDK types. Workers
receive an immutable `SharedState` and may return bounded claims and a final answer.
The synthesizer may produce an internal candidate answer for evaluation, but the
orchestrator's public result is explicitly `evaluation_only` and contains no answer.
Claims with the same key must have the same value.

## State and handoff rules

* Shared state is immutable at the contract boundary and advances by a monotonic
  revision.
* A handoff contains only task/worker IDs, state and permission digests, revision,
  and bounded claim keys; it never copies prompts, documents, secrets, or tool
  output.
* Handoff delivery is a replaceable transport. Synchronous delivery runs in a
  bounded worker thread and asynchronous delivery is deadline-bound; loss,
  alteration, timeout, or transport failure stops the candidate or invokes the
  explicitly configured single-agent fallback.
  A timeout cannot terminate the underlying synchronous callable already running in
  that thread, so only explicitly trusted side-effect-free callables are accepted.
* A worker may not change principal, authorization, approval request, action hash,
  or parameter hash. Any mismatch fails closed.

## Isolation, budgets, and failure behavior

Every worker is an isolated, trusted in-process evaluation-only protocol call. A
read-only capability token is required; no tool, network, or write capability is
supplied. A future production adapter must sandbox and authenticate workers. The
run has one combined budget for
worker calls, steps, retries, handoffs, and latency; workers cannot reset it. Routes
cannot repeat a worker, and a handoff limit prevents delegation loops. A failed
worker, invalid role/output, conflicting claim, or exhausted budget cannot produce a
successful multi-agent answer. A configured fallback may invoke the existing
single-agent boundary, and otherwise the result is terminal and content-free.

Approval context is carried by immutable digests. The orchestrator never executes a
tool and never converts a proposal into an action; Stage 13 approval remains the
only consequential execution boundary.

## Experiment and rollback

`compare_stage14_baseline` compares complete `BatchEvaluation` values using the
existing Stage 14 scorer. It requires exact scenario IDs, an authoritative
scenario-suite fingerprint recomputed from manifest content, matching
evaluation/corpus provenance, at least three complete
replicate artifact pairs with unique underlying baseline/candidate run IDs, an
authoritative scenario manifest whose content is used to recompute the suite
fingerprint, a strictly positive success improvement, bounded
latency/step ratios, known bounded cost, non-regressing tool/argument accuracy,
non-regressing escalation correctness (the Stage 14 failure surface), and 100%
approval compliance. It also requires a `StatisticalEvidence` artifact bound to
the exact replicate IDs, manifest fingerprint, and observed success delta; the
artifact must report a powered analysis (target/achieved power and p-value) and
be authoritative and independently verified. The
`BatchEvaluation.dataset_fingerprint` string alone is not trusted because a
model-copy/deserialization boundary can overwrite it; the
comparison retains single-agent when the authoritative manifest is unavailable.
A caller's replicate count cannot satisfy the evidence contract. It returns a
structured
decision and `rollback_decision` keeps the candidate disabled whenever any gate
fails. Candidate results are never cherry-picked.

This local reference slice records a retention decision: keep single-agent as the
default until a future matched run demonstrates the required benefit. The candidate
route remains available for controlled synthetic experiments only.

## Non-goals and limitations

No unbounded delegation, autonomous consequential action, chat memory, frontend,
provider-specific orchestration, durable distributed state, or production quality
claim is introduced. Handoff, worker state, and the default telemetry sink are
process-local; production use
would require durable state, distributed leases, authenticated worker identity, and
an independently reviewed experiment with enough repeated scenarios for statistical
confidence.
