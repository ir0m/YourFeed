"""Codex (`codex exec`) with the user's ChatGPT login. Shell, web and plugin tools are off."""

from __future__ import annotations

import json

from .base import LLMError, LLMResult, transcript
from .cli import Workdir, find, login_problem

DISABLED_FEATURES = ("shell_tool", "unified_exec", "plugins", "apps", "hooks", "browser_use",
                     "computer_use", "view_image")


def command(executable: str, model: str, schema_path: str, output_path: str) -> list[str]:
    argv = [executable, "exec", "--skip-git-repo-check", "--ephemeral", "--ignore-user-config",
            "--ignore-rules", "--sandbox", "read-only", "-c", 'web_search="disabled"', "--color", "never",
            "--json", "--output-schema", schema_path, "-o", output_path]
    for feature in DISABLED_FEATURES:
        argv += ["--disable", feature]
    if model:
        argv += ["-m", model]
    return [*argv, "-"]


def prompt(system: str, messages: list[dict[str, str]]) -> str:
    """`codex exec` has no system prompt option, so the instructions lead the prompt."""
    return f"<instructions>\n{system}\n</instructions>\n\n{transcript(messages)}"


class CodexCliClient:
    provider = "codex_cli"

    def __init__(self, model: str, executable: str | None = None):
        self.model = model
        self.executable = executable or find("codex")

    def complete(self, system: str, messages: list[dict[str, str]], schema: dict) -> LLMResult:
        with Workdir(self.executable) as workdir:
            schema_path = workdir.write("schema.json", json.dumps(schema))
            output_path = workdir.path / "last.txt"
            completed = workdir.run(command(self.executable, self.model, str(schema_path), str(output_path)),
                                    prompt(system, messages))
            text = output_path.read_text(encoding="utf-8") if output_path.exists() else ""
        input_tokens = output_tokens = 0
        failure = None
        for line in completed.stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "turn.completed":
                usage = event.get("usage") or {}
                input_tokens += int(usage.get("input_tokens") or 0)
                output_tokens += int(usage.get("output_tokens") or 0) + int(usage.get("reasoning_output_tokens") or 0)
            elif event.get("type") in {"turn.failed", "error"}:
                failure = json.dumps(event, ensure_ascii=False)
        if failure or (completed.returncode != 0 and not text):
            detail = (failure or "") + completed.stderr[-2000:]
            raise LLMError("cli_error", fatal=login_problem(detail))
        return LLMResult(text, input_tokens, output_tokens)
