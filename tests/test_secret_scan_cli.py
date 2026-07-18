"""Tests for scripts/secret_scan.py — the shared credential-scanner CLI.

The Vim/Neovim AI integration shells out to this before sending a payload to an
AI tool, so a secret is refused *at the editor* the same way the bash-review
hooks refuse it for Bash. It reuses `scan_secrets` from the hooks module (single
source of truth for the patterns); this test pins the CLI contract:

    exit 0  -> clean, nothing on stdout
    exit 1  -> credential detected, generic label on stdout (never the value)
"""

import io
import subprocess
import sys

import pytest
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "scripts"))
import secret_scan  # noqa: E402

SCANNER = REPO_ROOT / "scripts" / "secret_scan.py"


def _run_main(monkeypatch, capsys, text):
    monkeypatch.setattr("sys.stdin", io.StringIO(text))
    rc = secret_scan.main()
    return rc, capsys.readouterr().out


class TestMainInProcess:
    @pytest.mark.parametrize(
        "text",
        [
            "export API_KEY=sk-abcdefghijklmnopqrstuvwx",
            'curl -H "Authorization: Bearer ' + "e" * 24 + '"',
            "PGPASSWORD=Sup3r$ecret!2024 psql -h db",
            "ghp_" + "a" * 36,
            # A credential inside otherwise ordinary buffer content (the editor
            # sends selections / diffs, not shell commands).
            "const client = new S3({\n  secretAccessKey: 'AKIAIOSFODNN7EXAMPLE',\n})",
        ],
    )
    def test_credential_exits_1_with_generic_label(self, monkeypatch, capsys, text):
        rc, out = _run_main(monkeypatch, capsys, text)
        assert rc == 1
        assert out.strip()  # a non-empty generic category label
        assert text not in out  # the raw value/payload is never echoed back

    @pytest.mark.parametrize(
        "text",
        [
            "print('hello world')",
            "def add(a, b):\n    return a + b",
            "git commit -m 'fix token refresh logic'",
            "cat ~/.aws/credentials  # a sensitive PATH, not a value",
            "",
        ],
    )
    def test_clean_exits_0_silently(self, monkeypatch, capsys, text):
        rc, out = _run_main(monkeypatch, capsys, text)
        assert rc == 0
        assert out == ""

    def test_scanner_crash_exits_2_not_clean(self, monkeypatch, capsys):
        # An unexpected scanner failure must fail open as "unavailable" (exit 2,
        # distinct from a detection), never be misreported as clean (0).
        def boom(*_a, **_k):
            raise RuntimeError("scanner blew up")

        monkeypatch.setattr(secret_scan, "scan_secrets", boom)
        monkeypatch.setattr("sys.stdin", io.StringIO("anything"))
        rc = secret_scan.main()
        assert rc == 2
        assert capsys.readouterr().out == ""  # nothing on stdout


class TestCliSubprocess:
    """The editors invoke it as `python3 secret_scan.py` with the payload on
    stdin — never on argv (a secret on argv would leak via `ps`)."""

    def _run(self, payload):
        return subprocess.run(
            [sys.executable, str(SCANNER)],
            input=payload,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def test_secret_on_stdin_exits_1(self):
        r = self._run("token=" + "x" * 20)
        assert r.returncode == 1
        assert r.stdout.strip()

    def test_clean_on_stdin_exits_0(self):
        r = self._run("just some ordinary code without secrets")
        assert r.returncode == 0
        assert r.stdout == ""
