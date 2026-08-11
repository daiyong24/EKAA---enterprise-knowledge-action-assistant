import re
from typing import Final

from backend.app.llm.models import ChatResult, TokenUsage


class StageOneResponsePolicy:
    """Enforce the no-enterprise-evidence boundary before any provider call."""

    _ENTERPRISE_REFERENCE: Final = re.compile(
        r"\b(our|we|us|internal|company|organisation|organization|ekaa)\b",
        flags=re.IGNORECASE,
    )
    _CONCEPTUAL_EXPLANATION: Final = re.compile(
        r"\b(explain|define|describe|compare|teach|clarify)\b|^(how|why)\s+"
        r"(does|do|is|are)\b",
        flags=re.IGNORECASE,
    )
    _FACTUAL_LOOKUP: Final = re.compile(
        r"\b(who|where|when|which|whose)\b|\b(explain|describe)\s+"
        r"(what|who|where|when|which)\b|\b(current|latest|status|schedule|"
        r"policy|ceo|president|price|date)\b",
        flags=re.IGNORECASE,
    )
    _REFUSAL: Final = (
        "EKAA does not have an authorized knowledge base in this stage, so it "
        "cannot answer organization-specific questions. Insufficient information "
        "is available in the knowledge base."
    )
    _POLICY_MODEL: Final = "ekaa-stage-1-policy"

    def pre_provider_response(self, user_message: str) -> ChatResult | None:
        """Return a deterministic refusal unless the request is conceptual.

        Stage 1 supports only general conceptual explanations. It rejects clear
        entity and factual-lookup signals, policy/status requests, and explicit
        organization references. Stage 4 retrieval and Stage 9 authorization will
        replace this interim boundary.
        """
        if (
            not self._ENTERPRISE_REFERENCE.search(user_message)
            and not self._FACTUAL_LOOKUP.search(user_message)
            and self._CONCEPTUAL_EXPLANATION.search(user_message)
        ):
            return None
        return self._refusal()

    def post_provider_response(self, result: ChatResult) -> ChatResult:
        """Withhold all ungrounded provider text while preserving integration data.

        No content classifier can prove that arbitrary free text has no enterprise
        factual claim. Until retrieval and authorization exist, the public endpoint
        must therefore not expose provider-generated text at all.
        """
        return ChatResult(
            answer=(
                "EKAA completed the Stage 1 conceptual request, but it does not "
                "expose ungrounded model content. Grounded answers will be available "
                "after authorized retrieval is implemented."
            ),
            model=result.model,
            usage=result.usage,
        )

    def _refusal(self) -> ChatResult:
        return ChatResult(
            answer=self._REFUSAL,
            model=self._POLICY_MODEL,
            usage=TokenUsage(input_tokens=0, output_tokens=0, total_tokens=0),
        )
