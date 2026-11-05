from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class LLMResult:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    refused: bool = False


class LLMError(RuntimeError):
    """`fatal` errors (bad key, unknown model) stop model calls for the rest of the run."""

    def __init__(self, code: str, *, fatal: bool = False):
        self.code = code
        self.fatal = fatal
        super().__init__(code)


def transcript(messages: list[dict[str, str]]) -> str:
    """One user prompt that carries earlier turns, for providers called without history."""
    if len(messages) == 1:
        return messages[0]["content"]
    labels = {"user": "依頼", "assistant": "あなたの回答"}
    return "\n\n".join(f"[{labels[message['role']]}]\n{message['content']}" for message in messages)


class LLMClient(Protocol):
    provider: str
    model: str

    def complete(self, system: str, messages: list[dict[str, str]], schema: dict) -> LLMResult:
        """`messages` alternate user and assistant turns, starting with the user."""
        ...
