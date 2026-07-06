"""Drift guard: the two _bash_review_common.py copies must stay identical.

The shared hook module is installed to two different symlinked locations
(~/.claude/hooks and ~/.codex/hooks) and therefore lives as two tracked
files. This test fails loudly if they ever diverge (which is exactly how a
prior security fix was missed in one copy).
"""

from conftest import REPO_ROOT

CLAUDE_COPY = REPO_ROOT / ".claude/hooks/_bash_review_common.py"
CODEX_COPY = REPO_ROOT / ".codex/hooks/_bash_review_common.py"


def test_shared_module_copies_exist():
    assert CLAUDE_COPY.is_file()
    assert CODEX_COPY.is_file()


def test_shared_module_copies_are_byte_identical():
    assert CLAUDE_COPY.read_bytes() == CODEX_COPY.read_bytes()
