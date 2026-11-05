"""Gemini CLI in headless mode with the user's Google login. Every tool call is denied."""

from __future__ import annotations

import json
import os

from .base import LLMError, LLMResult, transcript
from .cli import Workdir, find, login_problem

# Admin-tier policy: the highest tier, so user or workspace settings cannot allow a tool.
DENY_ALL = '[[rule]]\ntoolName = "*"\ndecision = "deny"\npriority = 999\n'
NO_MCP_SERVER = "yourfeed-none"
AUTH_ERROR_CODE = 41
PASSED_ENV = ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_CLOUD_PROJECT", "GOOGLE_GENAI_USE_VERTEXAI",
              "GOOGLE_GENAI_USE_GCA")


def command(executable: str, model: str, policy_path: str) -> list[str]:
    argv = [executable, "--output-format", "json", "--admin-policy", policy_path,
            "--allowed-mcp-server-names", NO_MCP_SERVER, "--skip-trust",
            "-p", "JSONだけを返してください。"]
    if model:
        argv += ["-m", model]
    return argv


class GeminiCliClient:
    provider = "gemini_cli"

    def __init__(self, model: str, executable: str | None = None):
        self.model = model
        self.executable = executable or find("gemini")

    def complete(self, system: str, messages: list[dict[str, str]], schema: dict) -> LLMResult:
        with Workdir(self.executable) as workdir:
            policy = workdir.write("policy.toml", DENY_ALL)
            system_file = workdir.write("system.md", f"{system}\n\n出力するJSONのスキーマ:\n{json.dumps(schema)}")
            env = {"GEMINI_SYSTEM_MD": str(system_file),
                   **{name: os.environ[name] for name in PASSED_ENV if os.environ.get(name)}}
            completed = workdir.run(command(self.executable, self.model, str(policy)), transcript(messages), env)
        data = None
        for output in (completed.stdout, completed.stderr):
            try:
                data = json.loads(output)
                break
            except json.JSONDecodeError:
                continue
        if not isinstance(data, dict):
            raise LLMError("unexpected_response", fatal=login_problem(completed.stdout + completed.stderr))
        if data.get("error"):
            error = data["error"]
            fatal = completed.returncode == AUTH_ERROR_CODE or error.get("code") == AUTH_ERROR_CODE \
                or login_problem(json.dumps(error, ensure_ascii=False))
            raise LLMError("cli_error", fatal=fatal)
        input_tokens = output_tokens = 0
        for model_stats in ((data.get("stats") or {}).get("models") or {}).values():
            tokens = model_stats.get("tokens") or {}
            input_tokens += int(tokens.get("prompt") or 0)
            output_tokens += int(tokens.get("candidates") or 0) + int(tokens.get("thoughts") or 0)
        return LLMResult(data.get("response") or "", input_tokens, output_tokens)
