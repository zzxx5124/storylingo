"""Provider/model-aware chat-completions request profiles.

Profiles own only transport parameters.  They never alter the canonical
analysis schema or provider-neutral structured-output contract.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json


REQUEST_PROFILE_VERSION = "ai-request-profile-v2"


@dataclass(frozen=True)
class AIRequestProfile:
    name: str
    version: str
    completion_token_field: str
    temperature_policy: str
    temperature: float | None
    structured_output_capability: str
    minimum_completion_tokens: int = 0

    def __post_init__(self):
        if self.completion_token_field not in ("max_tokens", "max_completion_tokens"):
            raise ValueError("unsupported completion token field")
        if self.temperature_policy not in ("explicit", "omit"):
            raise ValueError("unsupported temperature policy")
        if self.temperature_policy == "explicit" and self.temperature is None:
            raise ValueError("explicit temperature policy requires a value")
        if self.structured_output_capability not in ("probe_required", "not_supported"):
            raise ValueError("unsupported structured output capability")

    @property
    def hash(self) -> str:
        payload = {
            "name": self.name,
            "version": self.version,
            "completionTokenField": self.completion_token_field,
            "temperaturePolicy": self.temperature_policy,
            "temperature": self.temperature,
            "structuredOutputCapability": self.structured_output_capability,
            "minimumCompletionTokens": self.minimum_completion_tokens,
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def request_kwargs(self, *, model: str, messages: list[dict], completion_tokens: int,
                       temperature: float | None = None, **extra) -> dict:
        """Build a chat-completions payload using this profile's transport rules."""
        kwargs = {
            "model": model,
            "messages": messages,
            self.completion_token_field: max(int(completion_tokens), self.minimum_completion_tokens),
            **extra,
        }
        if self.temperature_policy == "explicit":
            kwargs["temperature"] = self.temperature if temperature is None else temperature
        return kwargs


_DEFAULT = AIRequestProfile(
    name="chat-completions-default",
    version=REQUEST_PROFILE_VERSION,
    completion_token_field="max_tokens",
    temperature_policy="explicit",
    temperature=0.2,
    structured_output_capability="probe_required",
)

_OPENAI_GPT5 = AIRequestProfile(
    name="openai-gpt5-chat-completions",
    version=REQUEST_PROFILE_VERSION,
    completion_token_field="max_completion_tokens",
    temperature_policy="omit",
    temperature=None,
    structured_output_capability="probe_required",
    # GPT-5 models can consume completion budget before emitting structured
    # content.  This remains a bounded transport budget, not a schema change.
    minimum_completion_tokens=2048,
)


def resolve(provider: dict | None = None, model: str | None = None) -> AIRequestProfile:
    """Return the single versioned profile for a provider/model request.

    Model-family matching deliberately lives only here so analyzer, repairs,
    capability probes and cache identity cannot drift into scattered special
    cases.  Future adapters may add a provider-declared profile selection at
    this boundary without changing callers.
    """
    selected = str(model or (provider or {}).get("model") or "").strip().lower()
    provider_type = str((provider or {}).get("provider_type") or "").strip().lower()
    if selected.startswith("gpt-5") and provider_type in ("", "openai", "openai_compatible"):
        return _OPENAI_GPT5
    return _DEFAULT
