# Measured Results and Metric Source Audit

This table is the complete set of numbers presented by the portfolio package. The
validator recomputes and checks each source file's SHA-256 before accepting it.

| Result | Value | Versioned source | Interpretation |
| --- | ---: | --- | --- |
| Retrieval Recall@5 | 1.0 | `docs/evaluation/artifacts/synthetic_retrieval_baseline_v1.json` | Synthetic retrieval fixture only |
| Retrieval Hit Rate@5 | 1.0 | same artifact | Synthetic retrieval fixture only |
| Retrieval Precision@5 | 0.2 | same artifact | Five returned candidates, one expected match per question |
| Retrieval MRR | 1.0 | same artifact | First expected chunk ranked first in this fixture |
| Retrieval failures | 0 | same artifact | No failures in the recorded retrieval run |
| Single-agent task success | 3/3 (1.0) | `docs/evaluation/stage12_baseline_v1.json` | Deterministic contract suite, not population quality |
| Human correctness/faithfulness/citation | unavailable | Stage 8 scoring contract | Requires calibrated, source-bound review |
| Estimated provider cost | unavailable | Stage 8 artifact | No versioned price schedule supplied |
| Multi-agent adoption | not claimed | `docs/STAGE_19_REPORT.md` | Powered evidence and operational benefit are not established |

The retrieval artifact identifies run
`reviewed-local-synthetic-retrieval-v1`, dataset version `1.0.0`, and corpus SHA-256
`c2e4d529881a0b711c4f5c6cc42b2ef1f6ed14a71e56a0dfef5678100fce2fa6`. The portfolio
does not relabel this deterministic fixture as production latency, availability,
cost, or answer quality. The active policy is `1.1.0` from
`docs/evaluation/baseline_policy_v1.json`; the validator checks the policy file hash,
dataset/corpus identity, and embedded artifact provenance before accepting the run.

## Source-audit rule

Every future published metric must include a run ID, source path, source SHA-256,
dataset/corpus identity, configuration identity, and scope. If any binding is absent,
the number must be reported as unavailable rather than inferred.
