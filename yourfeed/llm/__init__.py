"""Model providers behind one small interface."""

from __future__ import annotations

from typing import Any

from yourfeed import config

from .base import LLMClient, LLMError, LLMResult

PROVIDERS = ("openai", "anthropic", "claude_cli", "codex_cli", "gemini_cli")
# CLIs use their own default model when none is set.
CLI_PROVIDERS = {"codex_cli", "gemini_cli"}
DEFAULT_MODELS = {"anthropic": "claude-opus-5-5", "claude_cli": "claude-opus-5-5"}


def make_client(settings: dict[str, Any]) -> LLMClient:
    provider = settings["llm_provider"]
    model = (settings.get("llm_model") or DEFAULT_MODELS.get(provider, "")).strip()
    if not model and provider not in CLI_PROVIDERS:
        raise LLMError("no_model", fatal=True)
    if provider == "openai":
        from .openai_compat import OpenAICompatibleClient
        return OpenAICompatibleClient(config.openai_base_url(), model, config.api_key("openai"))
    if provider == "anthropic":
        from .anthropic_api import AnthropicClient
        return AnthropicClient(model, config.api_key("anthropic"))
    if provider == "claude_cli":
        from .claude_cli import ClaudeCliClient
        return ClaudeCliClient(model)
    if provider == "codex_cli":
        from .codex_cli import CodexCliClient
        return CodexCliClient(model)
    if provider == "gemini_cli":
        from .gemini_cli import GeminiCliClient
        return GeminiCliClient(model)
    raise LLMError("unknown_provider", fatal=True)


__all__ = ["DEFAULT_MODELS", "LLMClient", "LLMError", "LLMResult", "PROVIDERS", "make_client"]
