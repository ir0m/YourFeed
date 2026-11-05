"""Chat Completions API: OpenAI, Gemini, OpenRouter, Ollama, LM Studio, vLLM and others."""

from __future__ import annotations

import time

import httpx

from .base import LLMError, LLMResult

# Tried in order; a server that rejects one format gets the next.
FORMATS = ("json_schema", "json_object", "none")
RETRY_WAITS = (2.0, 10.0)


class OpenAICompatibleClient:
    provider = "openai"

    def __init__(self, base_url: str, model: str, api_key: str | None = None, *,
                 client: httpx.Client | None = None, timeout: float = 300.0):
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.model = model
        self.api_key = api_key
        self.client = client or httpx.Client(timeout=timeout)
        self.format_index = 0

    def _body(self, system: str, messages: list[dict[str, str]], schema: dict) -> dict:
        body: dict = {"model": self.model,
                      "messages": [{"role": "system", "content": system}, *messages]}
        kind = FORMATS[self.format_index]
        if kind == "json_schema":
            body["response_format"] = {"type": "json_schema",
                                       "json_schema": {"name": "summary", "schema": schema, "strict": True}}
        elif kind == "json_object":
            body["response_format"] = {"type": "json_object"}
        return body

    def complete(self, system: str, messages: list[dict[str, str]], schema: dict) -> LLMResult:
        for wait in (*RETRY_WAITS, None):
            try:
                return self._complete(system, messages, schema)
            except LLMError as exc:
                if exc.fatal or wait is None:
                    raise
                time.sleep(getattr(exc, "retry_after", None) or wait)
        raise AssertionError("unreachable")

    def _complete(self, system: str, messages: list[dict[str, str]], schema: dict) -> LLMResult:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        while True:
            try:
                response = self.client.post(self.url, json=self._body(system, messages, schema), headers=headers)
            except httpx.TimeoutException as exc:
                raise LLMError("timeout") from exc
            except httpx.TransportError as exc:
                raise LLMError("connection_error") from exc
            if response.status_code in {400, 422} and self.format_index < len(FORMATS) - 1:
                self.format_index += 1
                continue
            break
        if response.status_code in {401, 403, 404}:
            raise LLMError(f"http_{response.status_code}", fatal=True)
        if response.status_code == 429 or response.status_code >= 500:
            error = LLMError(f"http_{response.status_code}")
            try:
                error.retry_after = min(float(response.headers.get("Retry-After", "")), 30.0)
            except ValueError:
                pass
            raise error
        if response.status_code >= 400:
            raise LLMError(f"http_{response.status_code}", fatal=True)
        try:
            data = response.json()
            choice = data["choices"][0]
            message = choice["message"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMError("unexpected_response") from exc
        usage = data.get("usage") or {}
        return LLMResult(text=message.get("content") or "",
                         input_tokens=int(usage.get("prompt_tokens") or 0),
                         output_tokens=int(usage.get("completion_tokens") or 0),
                         refused=bool(message.get("refusal")) or choice.get("finish_reason") == "content_filter")
