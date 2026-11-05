"""Claude Code in print mode (`claude -p`), with every tool turned off."""

from __future__ import annotations

import json

from .base import LLMError, LLMResult, transcript
from .cli import Workdir, find, login_problem


def command(model: str, system: str, schema: dict, executable: str = "claude") -> list[str]:
    return [executable, "-p", "--restricted", "--tools", "", "--strict-mcp-config",
            "--disable-slash-commands", "--no-session-persistence", "--output-format", "json",
            "--model", model, "--system-prompt", system, "--json-schema", json.dumps(schema)]


class ClaudeCliClient:
    provider = "claude_cli"

    def __init__(self, model: str, executable: str | None = None):
        self.model = model
        self.executable = executable or find("claude")

    def complete(self, system: str, messages: list[dict[str, str]], schema: dict) -> LLMResult:
        with Workdir(self.executable) as workdir:
            completed = workdir.run(command(self.model, system, schema, self.executable), transcript(messages))
        try:
            data = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise LLMError("unexpected_response",
                           fatal=login_problem(completed.stdout + completed.stderr)) from exc
        if data.get("is_error"):
            status = data.get("api_error_status")
            raise LLMError(f"cli_{data.get('subtype') or 'error'}",
                           fatal=status in {401, 403, 404} or login_problem(str(data.get("result"))))
        usage = data.get("usage") or {}
        input_tokens = sum(int(usage.get(key) or 0) for key in
                           ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
        structured = data.get("structured_output")
        text = json.dumps(structured, ensure_ascii=False) if structured is not None else data.get("result") or ""
        return LLMResult(text, input_tokens, int(usage.get("output_tokens") or 0),
                         refused=data.get("stop_reason") == "refusal")
