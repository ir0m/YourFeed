import json
import os
import stat
from types import SimpleNamespace

import httpx
import pytest

from yourfeed.llm import LLMError, make_client
from yourfeed.llm.anthropic_api import AnthropicClient
from yourfeed.llm.claude_cli import ClaudeCliClient, command
from yourfeed.llm.openai_compat import OpenAICompatibleClient
from yourfeed.pipeline.prompt import SCHEMA

REPLY = {"skip": False, "title": "t", "facts": "f", "interpretation": ""}
MESSAGES = [{"role": "user", "content": "本文"}]


def test_openai_compatible_falls_back_when_json_schema_is_rejected():
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append(body.get("response_format", {}).get("type"))
        if body.get("response_format", {}).get("type") == "json_schema":
            return httpx.Response(400, json={"error": "response_format not supported"})
        assert request.headers["Authorization"] == "Bearer key"
        assert body["messages"][0] == {"role": "system", "content": "sys"}
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(REPLY)}}],
                                         "usage": {"prompt_tokens": 12, "completion_tokens": 3}})

    llm = OpenAICompatibleClient("http://localhost:11434/v1/", "llama", "key",
                                 client=httpx.Client(transport=httpx.MockTransport(handler)))
    result = llm.complete("sys", MESSAGES, SCHEMA)
    assert json.loads(result.text) == REPLY and (result.input_tokens, result.output_tokens) == (12, 3)
    assert seen == ["json_schema", "json_object"]
    llm.complete("sys", MESSAGES, SCHEMA)
    assert seen[-1] == "json_object"


def test_openai_compatible_auth_errors_are_fatal():
    llm = OpenAICompatibleClient("https://api.example.com/v1", "m", None,
                                 client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(401))))
    with pytest.raises(LLMError) as caught:
        llm.complete("sys", MESSAGES, SCHEMA)
    assert caught.value.fatal


class FakeMessages:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def anthropic_response(stop_reason="end_turn"):
    return SimpleNamespace(
        stop_reason=stop_reason, content=[SimpleNamespace(type="text", text=json.dumps(REPLY))],
        usage=SimpleNamespace(input_tokens=5, cache_creation_input_tokens=10, cache_read_input_tokens=0,
                              output_tokens=7))


def test_anthropic_uses_structured_output_and_fallbacks_on_supported_models():
    stable, beta = FakeMessages(anthropic_response()), FakeMessages(anthropic_response())
    fake = SimpleNamespace(messages=stable, beta=SimpleNamespace(messages=beta))
    result = AnthropicClient("claude-opus-5-5", client=fake).complete("sys", MESSAGES, SCHEMA)
    assert json.loads(result.text) == REPLY and result.input_tokens == 15
    call = beta.calls[0]
    assert call["fallbacks"] == "default" and call["betas"] == ["server-side-fallback-2026-07-01"]
    assert call["output_config"]["format"]["schema"] == SCHEMA
    AnthropicClient("claude-haiku-5-5", client=fake).complete("sys", MESSAGES, SCHEMA)
    assert "fallbacks" not in stable.calls[0]


def test_anthropic_retry_is_one_prompt_and_refusals_are_flagged():
    beta = FakeMessages(anthropic_response("refusal"))
    fake = SimpleNamespace(messages=beta, beta=SimpleNamespace(messages=beta))
    turns = MESSAGES + [{"role": "assistant", "content": "{}"}, {"role": "user", "content": "直して"}]
    result = AnthropicClient("claude-opus-5-5", client=fake).complete("sys", turns, SCHEMA)
    assert result.refused
    sent = beta.calls[0]["messages"]
    assert len(sent) == 1 and sent[0]["role"] == "user" and "直して" in sent[0]["content"]


def test_claude_cli_runs_without_tools_and_reads_structured_output(tmp_path):
    assert command("m", "sys", SCHEMA)[3:5] == ["--tools", ""]
    script = tmp_path / "claude"
    script.write_text("#!/bin/sh\ncat > /dev/null\n" + "echo '" + json.dumps({
        "is_error": False, "structured_output": REPLY, "result": "ignored",
        "usage": {"input_tokens": 2, "cache_creation_input_tokens": 100, "output_tokens": 9}}) + "'\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    result = ClaudeCliClient("claude-opus-5-5", str(script)).complete("sys", MESSAGES, SCHEMA)
    assert json.loads(result.text) == REPLY and result.input_tokens == 102 and result.output_tokens == 9


def test_make_client_needs_a_model_for_openai(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(LLMError) as caught:
        make_client({"llm_provider": "openai", "llm_model": " "})
    assert caught.value.code == "no_model"
    assert make_client({"llm_provider": "claude_cli", "llm_model": ""}).model == "claude-opus-5-5"
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:11434/v1/")
    assert make_client({"llm_provider": "openai", "llm_model": "llama"}).url == "http://localhost:11434/v1/chat/completions"
    assert os.environ.get("OPENAI_API_KEY") is None


def fake_cli(tmp_path, name, body):
    script = tmp_path / name
    script.write_text("#!/bin/sh\n" + body)
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script)


def test_codex_cli_turns_tools_off_and_reads_the_last_message(tmp_path):
    from yourfeed.llm.codex_cli import CodexCliClient, command
    argv = command("codex", "", "/s.json", "/o.txt")
    for feature in ("shell_tool", "unified_exec", "plugins", "hooks"):
        assert ["--disable", feature] == argv[argv.index(feature) - 1:argv.index(feature) + 1]
    assert "--ignore-user-config" in argv and argv[argv.index("--sandbox") + 1] == "read-only"
    assert "-m" not in argv and command("codex", "gpt-x", "/s", "/o")[-3:] == ["-m", "gpt-x", "-"]
    executable = fake_cli(tmp_path, "codex", (
        'while [ "$1" != "-o" ]; do shift; done; out="$2"\n'
        "cat > /dev/null\n"
        f"echo '{json.dumps(REPLY)}' > \"$out\"\n"
        "echo '{\"type\":\"turn.completed\",\"usage\":{\"input_tokens\":30,\"output_tokens\":5,"
        "\"reasoning_output_tokens\":2}}'\n"))
    result = CodexCliClient("", executable).complete("sys", MESSAGES, SCHEMA)
    assert json.loads(result.text) == REPLY and (result.input_tokens, result.output_tokens) == (30, 7)


def test_codex_cli_login_failures_are_fatal(tmp_path):
    from yourfeed.llm.codex_cli import CodexCliClient
    executable = fake_cli(tmp_path, "codex", "cat > /dev/null\necho 'Not logged in' >&2\nexit 1\n")
    with pytest.raises(LLMError) as caught:
        CodexCliClient("", executable).complete("sys", MESSAGES, SCHEMA)
    assert caught.value.fatal


def test_gemini_cli_denies_all_tools_and_uses_our_system_prompt(tmp_path):
    from yourfeed.llm.gemini_cli import DENY_ALL, GeminiCliClient
    assert 'toolName = "*"' in DENY_ALL and 'decision = "deny"' in DENY_ALL
    log = tmp_path / "seen.txt"
    executable = fake_cli(tmp_path, "gemini", (
        f'cat > /dev/null\necho "$@" > {log}\ncat "$GEMINI_SYSTEM_MD" >> {log}\n'
        "echo '" + json.dumps({"response": json.dumps(REPLY), "stats": {"models": {"m": {"tokens": {
            "prompt": 40, "candidates": 6, "thoughts": 3}}}}}) + "'\n"))
    result = GeminiCliClient("gemini-x", executable).complete("要約の指示", MESSAGES, SCHEMA)
    assert json.loads(result.text) == REPLY and (result.input_tokens, result.output_tokens) == (40, 9)
    seen = log.read_text()
    assert "--admin-policy" in seen and "-m gemini-x" in seen and "要約の指示" in seen


def test_gemini_cli_auth_errors_are_fatal(tmp_path):
    from yourfeed.llm.gemini_cli import GeminiCliClient
    executable = fake_cli(tmp_path, "gemini", (
        "cat > /dev/null\necho '{\"error\":{\"message\":\"Please set an Auth method\",\"code\":41}}' >&2\nexit 41\n"))
    with pytest.raises(LLMError) as caught:
        GeminiCliClient("", executable).complete("sys", MESSAGES, SCHEMA)
    assert caught.value.fatal
