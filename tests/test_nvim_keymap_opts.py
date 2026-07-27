"""`vim.keymap.set/del` opts must spell the buffer option `buf`, not `buffer`.

Neovim 0.12 renamed the option; `runtime/lua/vim/keymap.lua` carries the
schedule in-tree:

    TODO(skewb1k): soft-deprecate `buffer` option in 0.13, remove in 0.15.

Commit de046d2 ("refactor: migrate deprecated Neovim 0.12 APIs") swept the whole
tree and its message spells the rule out: "Rename `buffer` to `buf` in
vim.keymap.set/del opts (deprecated-0.12)". Nothing then held the line, and two
later commits reintroduced the old spelling in code written after the sweep --
ad938a4 (ai/init.lua) and 51cb2a8 (ai/ui.lua), 23 sites between them. Both files
are inside luacheck's scope; luacheck reads names and scopes, not table keys, so
it cannot see an option key that is merely wrong. Only a text-level check can,
which is why this lives here rather than in .luacheckrc.

The check is an allowlist, not a pattern match on the call: `ai/init.lua` binds
keys through a `local map = vim.keymap.set` alias, so a rule keyed on the text
`keymap.set` would have a hole at exactly the two sites that drifted. Instead
every `buffer =` in the Lua tree is flagged and only `nvim_create_autocmd` --
where `buffer` is the correct and only spelling -- is excused.

A hand-pinned list of the three excused sites would also work, and would be
closer to how the rest of this suite pins things (SECRET_PATH_PATTERNS,
_GLOBBED_SOURCES). Classifying by the enclosing call was chosen instead so that
adding an ordinary autocmd does not require editing a test, at the cost of the
small comment/string scanner below; if that scanner ever misjudges something,
swapping in the pinned list is the simpler fix, not more scanner.
"""

import re

from conftest import REPO_ROOT

NVIM_LUA_DIR = REPO_ROOT / ".config/nvim"

# Calls whose opts table legitimately takes a `buffer` key. `vim.keymap.set` and
# `vim.keymap.del` are deliberately absent -- that is the whole point.
CALLS_ALLOWED_TO_USE_BUFFER = {"nvim_create_autocmd"}

_BUFFER_KEY = re.compile(r"\bbuffer\s*=")
# The identifier right before the enclosing "(", e.g. `vim.api.nvim_create_autocmd`.
_CALLEE = re.compile(r"([A-Za-z_][\w.:]*)\s*$")


def _blank_comments_and_strings(src: str) -> str:
    """Return `src` with comments and string literals replaced by spaces.

    Length and therefore every offset is preserved, so a match found in the
    blanked text points at the same place in the original. Blanking rather than
    deleting is what lets the paren walk below stay honest about "(" and ")"
    that only appear inside a comment or a string.
    """
    out = list(src)
    i, n = 0, len(src)

    def blank(start: int, end: int) -> None:
        for k in range(start, min(end, n)):
            if out[k] != "\n":
                out[k] = " "

    while i < n:
        two = src[i : i + 2]
        # Long bracket, as a comment (--[[ ... ]]) or as a string ([[ ... ]]).
        bracket_at = i + 2 if two == "--" and src[i + 2 : i + 4] == "[[" else None
        if bracket_at is None and two == "[[":
            bracket_at = i
        if bracket_at is not None:
            end = src.find("]]", bracket_at + 2)
            end = n if end == -1 else end + 2
            blank(i, end)
            i = end
            continue
        if two == "--":
            end = src.find("\n", i)
            end = n if end == -1 else end
            blank(i, end)
            i = end
            continue
        if src[i] in "\"'":
            quote, j = src[i], i + 1
            while j < n and src[j] != quote:
                j += 2 if src[j] == "\\" else 1
            end = min(j + 1, n)
            blank(i, end)
            i = end
            continue
        i += 1
    return "".join(out)


def _enclosing_callee(text: str, pos: int) -> str:
    """Name of the function whose argument list encloses `pos` ("" if none).

    Walks left from `pos` counting brackets, so a nested table or call in the
    same argument list does not confuse the answer.
    """
    depth = 0
    for i in range(pos - 1, -1, -1):
        ch = text[i]
        if ch in ")]}":
            depth += 1
        elif ch in "([{":
            if depth:
                depth -= 1
            elif ch == "(":
                m = _CALLEE.search(text[:i])
                return m.group(1) if m else ""
    return ""


def _offending_sites() -> list[str]:
    sites = []
    for path in sorted(NVIM_LUA_DIR.rglob("*.lua")):
        src = path.read_text(encoding="utf-8")
        code = _blank_comments_and_strings(src)
        for m in _BUFFER_KEY.finditer(code):
            callee = _enclosing_callee(code, m.start()).rsplit(".", 1)[-1]
            if callee in CALLS_ALLOWED_TO_USE_BUFFER:
                continue
            line = src.count("\n", 0, m.start()) + 1
            rel = path.relative_to(REPO_ROOT)
            sites.append(f"{rel}:{line}: {src.splitlines()[line - 1].strip()}")
    return sites


def test_keymap_opts_use_buf_not_buffer():
    offenders = _offending_sites()
    assert not offenders, (
        "`buffer` is the pre-0.12 spelling of the vim.keymap.set/del buffer "
        "option and is scheduled for removal in Neovim 0.15 (see "
        "runtime/lua/vim/keymap.lua). Commit de046d2 migrated the tree to "
        "`buf`; these sites reintroduce the old name:\n  " + "\n  ".join(offenders)
    )


def test_autocmd_buffer_sites_still_exist():
    """Keep the allowlist honest: the check must not be passing vacuously.

    `nvim_create_autocmd` legitimately takes `buffer`, so those sites are
    excused above. If they ever disappear, the excuse is unused and the
    allowlist should shrink with them rather than sit there hiding a future
    mistake.
    """
    excused = 0
    for path in NVIM_LUA_DIR.rglob("*.lua"):
        code = _blank_comments_and_strings(path.read_text(encoding="utf-8"))
        for m in _BUFFER_KEY.finditer(code):
            callee = _enclosing_callee(code, m.start()).rsplit(".", 1)[-1]
            if callee in CALLS_ALLOWED_TO_USE_BUFFER:
                excused += 1
    assert excused, (
        "no nvim_create_autocmd site uses `buffer` any more, so "
        "CALLS_ALLOWED_TO_USE_BUFFER excuses nothing -- drop the allowlist "
        "entry (and this test) instead of leaving a dead exemption behind."
    )
