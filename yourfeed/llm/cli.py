"""Shared pieces for model CLIs the user is logged in to (subscription plans)."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from .base import LLMError

TIMEOUT_SECONDS = 600


def find(name: str) -> str:
    return shutil.which(name) or str(Path.home() / ".local" / "bin" / name)


class Workdir:
    """An empty working directory and a minimal environment for one CLI call."""

    def __init__(self, executable: str) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="yourfeed-")
        self.path = Path(self._tmp.name)
        home = os.environ.get("HOME", str(Path.home()))
        # npm-installed CLIs start with `#!/usr/bin/env node`, so node's directory is kept too.
        node = shutil.which("node")
        dirs = [str(Path(executable).parent), *([str(Path(node).parent)] if node else []),
                "/usr/local/bin", "/usr/bin", "/bin"]
        self.env = {"HOME": home, "PATH": ":".join(dict.fromkeys(dirs)), "LANG": "C.UTF-8"}

    def __enter__(self) -> Workdir:
        return self

    def __exit__(self, *_exc: object) -> None:
        self._tmp.cleanup()

    def write(self, name: str, content: str) -> Path:
        path = self.path / name
        path.write_text(content, encoding="utf-8")
        return path

    def run(self, argv: list[str], prompt: str, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
        try:
            completed = subprocess.run(argv, input=prompt, cwd=self.path, env={**self.env, **(extra_env or {})},
                                       capture_output=True, text=True, timeout=TIMEOUT_SECONDS)
            if completed.returncode == 127:
                raise LLMError("cli_not_found", fatal=True)
            return completed
        except FileNotFoundError as exc:
            raise LLMError("cli_not_found", fatal=True) from exc
        except subprocess.TimeoutExpired as exc:
            raise LLMError("timeout") from exc


def login_problem(text: str) -> bool:
    lowered = text.lower()
    return any(word in lowered for word in ("not logged in", "login", "log in", "authenticat", "unauthorized",
                                            "api key", "credential"))
