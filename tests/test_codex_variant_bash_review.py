"""Tests for .codex/hooks/bash-review.py.

Codex does not understand permissionDecision JSON, so the Codex variant
communicates only via exit codes (same contract as the other .codex
hooks): allow paths exit 0 silently, blocking paths (ask/deny) exit 2
with the reason on stderr. Nothing is ever printed to stdout.
"""

import io
import subprocess
import sys
import types
from urllib.error import URLError

from conftest import REPO_ROOT, fake_gemini, fake_run, hook_payload

HOOK = ".codex/hooks/bash-review.py"


def _run_raw(hook, raw, capsys, monkeypatch):
    """Execute a hook against arbitrary raw stdin bytes (malformed-input tests)."""
    hook_path = REPO_ROOT / hook
    code = compile(hook_path.read_text(encoding="utf-8"), str(hook_path), "exec")
    capsys.readouterr()
    monkeypatch.setattr(sys, "stdin", types.SimpleNamespace(buffer=io.BytesIO(raw)))
    monkeypatch.setattr("platform.system", lambda: "TestOS")
    g = {"__name__": "__main__", "__file__": str(hook_path)}
    exit_code = None
    try:
        exec(code, g)  # noqa: S102  # nosec B102
    except SystemExit as e:
        exit_code = e.code if e.code is not None else 0
    return exit_code, capsys.readouterr()


class TestAllowPaths:
    def test_safe_command_exits_zero_silently(self, run_hook):
        res = run_hook(HOOK, hook_payload("git branch"))
        assert res.exit_code == 0
        assert res.stdout == ""
        assert res.stderr == ""

    def test_gemini_allow_exits_zero_silently(self, run_hook):
        res = run_hook(HOOK, hook_payload("make build"), urlopen=fake_gemini("ALLOW"))
        assert res.exit_code == 0
        assert res.stdout == ""
        assert res.stderr == ""

    def test_codex_allow_exits_zero_silently(self, run_hook):
        res = run_hook(
            HOOK,
            hook_payload("make build"),
            urlopen=fake_gemini("ASK"),
            run=fake_run(stdout="ALLOW"),
        )
        assert res.exit_code == 0
        assert res.stdout == ""
        assert res.stderr == ""


class TestBlockingPaths:
    def test_pre_denied_command_blocks_with_stderr(self, run_hook):
        res = run_hook(HOOK, hook_payload("curl http://evil"))
        assert res.exit_code == 2
        assert res.stdout == ""
        assert "curl" in res.stderr

    def test_deny_hidden_after_newline_blocks(self, run_hook):
        # Parity with the claude variant: a denied command hidden past a
        # newline must block (exit 2), not reach single-model review.
        res = run_hook(HOOK, hook_payload("ls\nsudo rm -rf /"))
        assert res.exit_code == 2
        assert "sudo" in res.stderr

    def test_sudo_is_pre_denied(self, run_hook):
        res = run_hook(HOOK, hook_payload("sudo reboot"))
        assert res.exit_code == 2
        assert "sudo" in res.stderr

    def test_codex_ask_blocks_with_stderr(self, run_hook):
        res = run_hook(
            HOOK,
            hook_payload("make build"),
            urlopen=fake_gemini("ASK"),
            run=fake_run(stdout="ASK"),
        )
        assert res.exit_code == 2
        assert res.stdout == ""
        assert "Codex requires confirmation" in res.stderr

    def test_codex_deny_blocks_with_stderr(self, run_hook):
        res = run_hook(
            HOOK,
            hook_payload("make build"),
            urlopen=fake_gemini("ASK"),
            run=fake_run(stdout="DENY: destructive"),
        )
        assert res.exit_code == 2
        assert res.stdout == ""
        assert "Codex denied" in res.stderr

    def test_codex_error_with_gemini_ask_blocks(self, run_hook):
        res = run_hook(
            HOOK,
            hook_payload("make build"),
            urlopen=fake_gemini("ASK"),
            run=fake_run(returncode=1, stderr="codex down"),
        )
        assert res.exit_code == 2
        assert "Codex unavailable" in res.stderr

    def test_codex_error_with_gemini_deny_blocks(self, run_hook):
        res = run_hook(
            HOOK,
            hook_payload("make build"),
            urlopen=fake_gemini("DENY: risky"),
            run=fake_run(returncode=1, stderr="codex down"),
        )
        assert res.exit_code == 2
        assert res.stdout == ""
        assert "Codex unavailable" in res.stderr


class TestHighRisk:
    """High-risk commands are an AND-gate through the codex variant too: a
    unanimous ALLOW exits 0 (auto-execute), anything short of that blocks
    (exit 2) with both verdicts on stderr so the calling agent can relay them.
    """

    def test_both_allow_exits_zero(self, run_hook):
        res = run_hook(
            HOOK,
            hook_payload("npm install left-pad"),
            urlopen=fake_gemini("ALLOW"),
            run=fake_run(stdout="ALLOW"),
        )
        assert res.exit_code == 0
        assert res.stdout == ""
        assert res.stderr == ""

    def test_split_verdict_blocks_with_verdicts(self, run_hook):
        res = run_hook(
            HOOK,
            hook_payload("npm install left-pad"),
            urlopen=fake_gemini("ALLOW"),
            run=fake_run(stdout="ASK"),
        )
        assert res.exit_code == 2
        assert res.stdout == ""
        assert "High-risk" in res.stderr
        assert "Gemini=ALLOW" in res.stderr
        assert "Codex=ASK" in res.stderr

    def test_unanimous_deny_blocks(self, run_hook):
        res = run_hook(
            HOOK,
            hook_payload("git push --force origin main"),
            urlopen=fake_gemini("DENY: history rewrite"),
            run=fake_run(stdout="DENY: destructive"),
        )
        assert res.exit_code == 2
        assert "Gemini=DENY" in res.stderr
        assert "Codex=DENY" in res.stderr

    def test_codex_error_never_allows_high_risk(self, run_hook):
        res = run_hook(
            HOOK,
            hook_payload("rm -rf node_modules"),
            urlopen=fake_gemini("ALLOW"),
            run=fake_run(returncode=1, stderr="codex down"),
        )
        assert res.exit_code == 2
        assert "Codex=ERROR" in res.stderr


class TestDenyOverrideRemoved:
    """Parity with the claude variant: an explicit Gemini DENY is never
    silently overridden by a lone Codex ALLOW — the disagreement blocks
    (exit 2) with both verdicts on stderr.
    """

    def test_gemini_deny_codex_allow_blocks(self, run_hook):
        res = run_hook(
            HOOK,
            hook_payload("make deploy"),
            urlopen=fake_gemini("DENY: looks risky"),
            run=fake_run(stdout="ALLOW"),
        )
        assert res.exit_code == 2
        assert res.stdout == ""
        assert "Gemini=DENY" in res.stderr

    def test_gemini_ask_codex_allow_still_exits_zero(self, run_hook):
        res = run_hook(
            HOOK,
            hook_payload("make deploy"),
            urlopen=fake_gemini("ASK"),
            run=fake_run(stdout="ALLOW"),
        )
        assert res.exit_code == 0
        assert res.stdout == ""
        assert res.stderr == ""


class TestErrorFallbacks:
    """Abnormal Gemini/Codex paths through the .codex entry (exit-code contract).

    Parity with tests/test_bash_review.py's TestCodexStage error cases, but
    asserted against the codex variant's exit-2 + stderr contract instead of
    permissionDecision JSON. Exercises the review logic now shared in
    _bash_review_common.py through the codex-specific wrapper/main flow.
    """

    def test_missing_api_key_and_codex_missing_blocks(self, run_hook):
        # No GEMINI_API_KEY -> Gemini returns ERROR without any network call,
        # so it falls through to Codex; Codex unavailable -> block (ask).
        res = run_hook(
            HOOK,
            hook_payload("make deploy"),
            env={"GEMINI_API_KEY": None},
            run=fake_run(exc=FileNotFoundError("codex not found")),
        )
        assert res.exit_code == 2
        assert res.stdout == ""
        assert "Codex unavailable" in res.stderr

    def test_missing_api_key_codex_allow_exits_zero(self, run_hook):
        # Missing key sends Gemini to ERROR; Codex ALLOW overrides -> exit 0.
        res = run_hook(
            HOOK,
            hook_payload("make deploy"),
            env={"GEMINI_API_KEY": None},
            run=fake_run(stdout="ALLOW"),
        )
        assert res.exit_code == 0
        assert res.stdout == ""
        assert res.stderr == ""

    def test_gemini_primary_and_fallback_fail_then_codex_allow(self, run_hook):
        # Both the primary and flash-fallback Gemini calls raise -> Gemini
        # ERROR; Codex is then consulted and its ALLOW wins (exit 0).
        calls = []
        res = run_hook(
            HOOK,
            hook_payload("make deploy"),
            urlopen=fake_gemini(URLError("primary"), URLError("fallback"), calls=calls),
            run=fake_run(stdout="ALLOW"),
        )
        assert res.exit_code == 0
        assert res.stdout == ""
        assert res.stderr == ""
        assert len(calls) == 2
        assert "primary-model" in calls[0].full_url
        assert "fallback-model" in calls[1].full_url

    def test_codex_timeout_with_gemini_ask_blocks(self, run_hook):
        res = run_hook(
            HOOK,
            hook_payload("make deploy"),
            urlopen=fake_gemini("ASK"),
            run=fake_run(exc=subprocess.TimeoutExpired(cmd="codex", timeout=60)),
        )
        assert res.exit_code == 2
        assert res.stdout == ""
        assert "Codex unavailable" in res.stderr

    def test_codex_missing_with_gemini_ask_blocks(self, run_hook):
        res = run_hook(
            HOOK,
            hook_payload("make deploy"),
            urlopen=fake_gemini("ASK"),
            run=fake_run(exc=FileNotFoundError("codex not found")),
        )
        assert res.exit_code == 2
        assert res.stdout == ""
        assert "Codex unavailable" in res.stderr


class TestLogs:
    def test_detail_log_filename_is_nanosecond_and_pid_unique(self, run_hook):
        """Codex variant parity: nanosecond + PID uniqueness (bash_cmd_<sec>.log
        collided within a second and overwrote earlier audit logs)."""
        import os

        res = run_hook(HOOK, hook_payload("git branch"))
        detail_dir = res.fake_tmp / "codex_hooks/logs/PreToolUse/Bash/bash-review"
        names = [p.name for p in detail_dir.iterdir()]
        assert len(names) == 1
        name = names[0]
        assert name.startswith("bash_cmd_")
        assert name.endswith(f"_{os.getpid()}.log")


class TestMalformedInput:
    """Malformed hook input must block (exit 2) toward a human, not crash."""

    def test_non_dict_tool_input_blocks(self, run_hook):
        res = run_hook(HOOK, {"tool_name": "Bash", "tool_input": "notadict"})
        assert res.exit_code == 2
        assert res.stdout == ""
        assert res.stderr != ""

    def test_empty_stdin_blocks(self, capsys, monkeypatch):
        exit_code, captured = _run_raw(HOOK, b"", capsys, monkeypatch)
        assert exit_code == 2
        assert captured.err != ""

    def test_garbage_bytes_blocks(self, capsys, monkeypatch):
        exit_code, captured = _run_raw(
            HOOK, b"garbage not json {[", capsys, monkeypatch
        )
        assert exit_code == 2
        assert captured.err != ""


def _raise_notify(*args, **kwargs):
    raise RuntimeError("notify boom (post-decision side effect)")


class TestPostDecisionSideEffect:
    """A failure in post-decision bookkeeping (logging/notify) must not flip an
    already-decided exit code: a blocked command stays exit 2, and an approved
    command stays exit 0 (a cosmetic notify failure must not become a false
    block). Mirrors tests/test_bash_review.py's TestPostDecisionSideEffect for
    the codex variant's exit-code contract.
    """

    def test_notify_failure_keeps_block(self, run_hook, monkeypatch):
        import _bash_review_common as common

        monkeypatch.setattr(common, "notify", _raise_notify)
        res = run_hook(HOOK, hook_payload("curl http://evil"))
        assert res.exit_code == 2
        assert "curl" in res.stderr

    def test_notify_failure_keeps_allow(self, run_hook, monkeypatch):
        import _bash_review_common as common

        monkeypatch.setattr(common, "notify", _raise_notify)
        res = run_hook(HOOK, hook_payload("make build"), urlopen=fake_gemini("ALLOW"))
        assert res.exit_code == 0
        assert res.stdout == ""
        assert res.stderr == ""


class TestSecretPreScanGuard:
    """A command carrying a credential must block (exit 2) BEFORE any LLM call.
    The run_hook fixture raises on urllib/subprocess by default, so passing
    without fakes proves nothing was forwarded to Gemini / Codex. Mirrors the
    Claude variant's TestSecretPreScanGuard for the exit-code contract.
    """

    def test_secret_command_blocks_without_calling_any_llm(self, run_hook):
        secret = "g" * 16
        # No urlopen/run fakes: any outbound call would raise AssertionError.
        res = run_hook(HOOK, hook_payload(f"export API_KEY={secret}"))
        assert res.exit_code == 2
        assert res.stdout == ""
        assert "credential" in res.stderr.lower()
        assert secret not in res.stderr  # value never echoed back

    def test_detected_secret_is_redacted_in_local_logs(self, run_hook):
        secret = "j" * 20
        res = run_hook(HOOK, hook_payload(f"export TOKEN={secret}"))
        assert res.exit_code == 2
        summary = (res.home / ".codex/logs/bash-review.log").read_text(encoding="utf-8")
        assert secret not in summary
        detail_dir = res.fake_tmp / "codex_hooks/logs/PreToolUse/Bash/bash-review"
        detail_text = next(detail_dir.iterdir()).read_text(encoding="utf-8")
        assert secret not in detail_text
        assert "REDACTED" in detail_text

    def test_sensitive_path_without_value_still_reaches_review(self, run_hook):
        res = run_hook(
            HOOK,
            hook_payload("grep AWS_SECRET .env.local"),
            urlopen=fake_gemini("ALLOW"),
        )
        assert res.exit_code == 0
        assert res.stderr == ""


class TestAgentBlockDirective:
    """Every block reason carries an agent-facing directive telling Codex to
    report to the user and NOT route around the block with a simpler variant.

    Codex has no `ask` primitive (returning permissionDecision `ask` is
    unsupported and fails open), and it treats an exit-2 block as advisory
    feedback: rather than surfacing the flagged intent to a human, it tends to
    re-issue a simpler command that passes independent review. The directive
    discourages that autonomous work-around. Allow paths stay silent.
    """

    def test_pre_deny_block_carries_directive(self, run_hook):
        res = run_hook(HOOK, hook_payload("curl http://evil"))
        assert res.exit_code == 2
        assert "curl" in res.stderr  # original reason preserved
        assert "do NOT retry" in res.stderr
        assert "report" in res.stderr.lower()

    def test_split_verdict_block_carries_directive(self, run_hook):
        res = run_hook(
            HOOK,
            hook_payload("make deploy"),
            urlopen=fake_gemini("DENY: looks risky"),
            run=fake_run(stdout="ALLOW"),
        )
        assert res.exit_code == 2
        assert "Gemini=DENY" in res.stderr  # original reason preserved
        assert "do NOT retry" in res.stderr

    def test_allow_path_has_no_directive(self, run_hook):
        res = run_hook(HOOK, hook_payload("make build"), urlopen=fake_gemini("ALLOW"))
        assert res.exit_code == 0
        assert res.stderr == ""
        assert "do NOT retry" not in res.stderr
