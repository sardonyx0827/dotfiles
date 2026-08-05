"""Tests for scripts/secret_scan.py — the shared credential-scanner CLI.

The Vim/Neovim AI integration shells out to this before sending a payload to an
AI tool, so a secret is refused *at the editor* the same way the bash-review
hooks refuse it for Bash. It reuses `scan_secrets` from the hooks module (single
source of truth for the patterns); this test pins the CLI contract:

    exit 0  -> clean, nothing on stdout
    exit 1  -> credential detected, generic label on stdout (never the value)
    exit 2  -> the scan did not happen (import failed, stdin could not be read
               at all, or the scanner raised); callers fail open with a warning

Bytes that are not valid UTF-8 are not in that third state: they are decoded
with replacement and scanned, so the exit code follows the payload rather than
the ambient locale's error handler.
"""

import io
import os
import subprocess
import sys
import types

import pytest
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "scripts"))
import secret_scan  # noqa: E402

SCANNER = REPO_ROOT / "scripts" / "secret_scan.py"


def _stdin_from(data: bytes):
    """A stdin stand-in carrying the same text/`.buffer` pair the real one has."""
    return io.TextIOWrapper(io.BytesIO(data), encoding="utf-8")


def _run_main_bytes(monkeypatch, capsys, data: bytes):
    monkeypatch.setattr("sys.stdin", _stdin_from(data))
    rc = secret_scan.main()
    return rc, capsys.readouterr().out


def _run_main(monkeypatch, capsys, text):
    monkeypatch.setattr("sys.stdin", _stdin_from(text.encode("utf-8")))
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

    def test_undecodable_bytes_are_scanned_not_refused(self, monkeypatch, capsys):
        # Bytes that are not valid UTF-8 are decoded with replacement rather
        # than raising, so the scan still runs over everything that IS text.
        rc, out = _run_main_bytes(monkeypatch, capsys, b"\xff\xfe\x00 print(1)")
        assert rc == 0
        assert out == ""

    def test_a_secret_inside_undecodable_bytes_is_still_detected(
        self, monkeypatch, capsys
    ):
        # The point of decoding with replacement: a token pasted into an
        # otherwise binary buffer must not escape the scan.
        rc, out = _run_main_bytes(
            monkeypatch, capsys, b"\xff\xfe ghp_" + b"a" * 36 + b" \x00"
        )
        assert rc == 1
        assert out.strip()

    def test_a_read_failure_exits_2_not_1(self, monkeypatch, capsys):
        # A read that fails outright is the scanner being unavailable (2), not a
        # detection. Reporting 1 with nothing on stdout gave the editors an
        # empty-label confirm dialog -- a question that says nothing.
        class Unreadable:
            def read(self):
                raise OSError("stdin went away")

        monkeypatch.setattr("sys.stdin", types.SimpleNamespace(buffer=Unreadable()))
        rc = secret_scan.main()
        assert rc == 2
        assert capsys.readouterr().out == ""

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

    def _run_bytes(self, data, env=None):
        return subprocess.run(
            [sys.executable, str(SCANNER)],
            input=data,
            capture_output=True,
            timeout=30,
            env=env,
        )

    # The two error handlers sys.stdin picks up on its own: strict under a
    # UTF-8 locale, surrogateescape under C/POSIX (PEP 538). Set explicitly
    # rather than via LANG so the contrast holds on any host -- passing a bare
    # env with no locale vars silently lands on C for BOTH cases.
    @pytest.mark.parametrize(
        "stdin_env",
        [{"PYTHONIOENCODING": "utf-8:strict"}, {"LC_ALL": "C", "LANG": "C"}],
        ids=["strict", "surrogateescape"],
    )
    def test_undecodable_bytes_behave_the_same_under_either_handler(self, stdin_env):
        # Reading through the text layer made the exit code depend on the
        # environment rather than the payload: the same buffer decoded silently
        # in CI and raised on a developer's machine, where the raise exited 1
        # and the editors read that as a detection.
        env = {"PATH": os.environ["PATH"], **stdin_env}
        clean = self._run_bytes(b"\xff\xfe\x00 print(1)", env=env)
        assert clean.returncode == 0, clean.stderr
        assert clean.stdout == b""
        secret = self._run_bytes(b"\xff\xfe ghp_" + b"a" * 36, env=env)
        assert secret.returncode == 1, secret.stderr
        assert secret.stdout.strip()
