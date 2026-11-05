"""Claude API through the official SDK, with structured outputs."""

from __future__ import annotations

import anthropic

from .base import LLMError, LLMResult, transcript

# Models that accept server-side fallback on a refusal ("default" routing).
FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
MAX_TOKENS = 16000


class AnthropicClient:
    provider = "anthropic"

    def __init__(self, model: str, api_key: str | None = None, *, client: anthropic.Anthropic | None = None):
        self.model = model
        self.client = client or anthropic.Anthropic(api_key=api_key, timeout=300.0, max_retries=2)

    def complete(self, system: str, messages: list[dict[str, str]], schema: dict) -> LLMResult:
        request = {"model": self.model, "max_tokens": MAX_TOKENS, "system": system,
                   # A retry is sent as one new prompt rather than an edited history.
                   "messages": [{"role": "user", "content": transcript(messages)}],
                   "output_config": {"format": {"type": "json_schema", "schema": schema}}}
        try:
            if self.model in FALLBACK_MODELS:
                response = self.client.beta.messages.create(
                    betas=["server-side-fallback-2026-07-01"], fallbacks="default", **request)
            else:
                response = self.client.messages.create(**request)
        except anthropic.AuthenticationError as exc:
            raise LLMError("http_401", fatal=True) from exc
        except anthropic.PermissionDeniedError as exc:
            raise LLMError("http_403", fatal=True) from exc
        except anthropic.NotFoundError as exc:
            raise LLMError("http_404", fatal=True) from exc
        except anthropic.BadRequestError as exc:
            raise LLMError("http_400", fatal=True) from exc
        except anthropic.RateLimitError as exc:
            raise LLMError("http_429") from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(f"http_{exc.status_code}", fatal=exc.status_code < 500) from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError("connection_error") from exc
        usage = response.usage
        input_tokens = (usage.input_tokens + (usage.cache_creation_input_tokens or 0)
                        + (usage.cache_read_input_tokens or 0))
        if response.stop_reason == "refusal":
            return LLMResult("", input_tokens, usage.output_tokens, refused=True)
        text = next((block.text for block in response.content if block.type == "text"), "")
        return LLMResult(text, input_tokens, usage.output_tokens)
