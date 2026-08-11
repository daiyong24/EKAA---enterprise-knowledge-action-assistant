from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field


class TokenUsage(BaseModel):
    """Normalized provider token accounting exposed by the public API."""

    model_config = ConfigDict(frozen=True)

    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)


class ChatResult(BaseModel):
    """Structured application result independent of any provider response shape."""

    model_config = ConfigDict(frozen=True)

    answer: str = Field(min_length=1)
    model: str = Field(min_length=1)
    usage: TokenUsage


@dataclass(frozen=True)
class ChatPrompt:
    """The provider-ready prompt built from an untrusted user message."""

    system: str
    user: str
