"""Scrub what a shared dossier must not carry, and count what was removed.

Share mode works on two levels:

* Exclusion (an allow-list, enforced by the renderer): thinking, every
  non-human message (injected context, inter-agent messages, command output),
  attachments, API request records, session metadata, system records and
  unknown raw records are never rendered; the renderer reports each one here so
  the page can say how many were left out.
* Replacement (this module): every string that is rendered passes ``scrub``,
  which removes system-reminder blocks and private-key blocks whole, then
  replaces secrets, e-mail addresses, home directories (as paths, as a project
  slug, and the bare user name), Claude's temp directory and git identities.

Secret detection starts from the bash-review hook's ``_SECRET_SCANNERS`` so
the two lists cannot drift, then adds share-only rules. The hook's list is
tuned for precision (a false hit blocks a command); a shared page needs
recall instead, so the extra rules redact key=value secrets, Authorization and
Cookie headers in escaped forms, and name/value secret pairs. If the hook's
list cannot be loaded, share mode refuses to run.

Every pattern applied to transcript text starts at a literal and bounds its
repeats: tool outputs run to megabytes, and one unbounded pattern took 9 s on
100k characters.
"""

from __future__ import annotations

import bisect
import importlib.util
import re
from collections import Counter
from pathlib import Path
from typing import Any

EXCLUDE_LABELS = {
    "thinking": "thinking ブロック",
    "attachment": "attachment（CLAUDE.md・system-reminder などの注入）",
    "api_request": "API リクエストの記録",
    "meta": "権限・モード・system などのメタ情報",
    "injected": "人間以外からのテキスト（注入・要約・エージェント間の通知・コマンド出力）",
    "image": "画像",
    "raw_json": "未知の型の生データ",
}
REPLACE_LABELS = {
    "system_reminder": "<system-reminder> の除去",
    "secret": "シークレットらしい文字列",
    "email": "メールアドレス",
    "home": "ホームディレクトリのパス",
    "home_slug": "ホームディレクトリ由来のプロジェクト名",
    "username": "ユーザー名（ホームディレクトリ名）",
    "tmp_dir": "Claude の一時ディレクトリ",
    "git_author": "git の author 名",
}
REDACTED = "[REDACTED]"
_MIN_AUTHOR_LEN = 2
_MIN_USERNAME_LEN = 3

_REMINDER = re.compile(r"<system-reminder>.*?(?:</system-reminder>|\Z)", re.S)
# The hook's scanner matches only the BEGIN line; a shared page must lose the
# body and the END line too, also when newlines arrive JSON-escaped.
# A whole key block, also when its newlines arrive JSON-escaped. Bounded so a
# BEGIN line with no END cannot reach across megabytes; that case is handled
# by _redact_key_heads, which takes only the base64 lines that follow.
# A key block is matched by pairing each BEGIN line with the first END line
# after it (within 64 KiB), found by bisecting a single list of END positions:
# a lazy BEGIN.*?END regex rescans the same span for every repeated BEGIN.
_KEY_BLOCK_SPAN = 65_536
_PRIVATE_KEY_BEGIN = re.compile(
    r"-----BEGIN [A-Z0-9 ]{0,40}PRIVATE KEY(?: BLOCK)?-----"
)
_BASE64_CHARS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/="
)
# A key whose BEGIN line is elsewhere (split across strings, cut off) still
# ends in an END line; the body before it is walked back over in plain Python.
_PRIVATE_KEY_END = re.compile(r"-----END [A-Z0-9 ]{0,40}PRIVATE KEY(?: BLOCK)?-----")
_KEY_BODY_CHARS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=\\\r\n"
)
# Bounded by the RFC 5321 limits (64-char local part, 63-char labels). An
# unbounded `[...]+@` re-scans the whole run from every start position.
_EMAIL = re.compile(
    r"[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]{1,63}(?:\.[A-Za-z0-9-]{1,63}){0,8}"
    r"\.[A-Za-z]{2,24}"
)
_EMAIL_LOCAL_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._%+-"
)
_TMP_DIR = re.compile(r"(?:/private)?/tmp/claude-\d+(?!\d)")
_OTHER_HOME = re.compile(
    r"(?<![A-Za-z0-9_.-])/(Users|home)/(?!\[user\])(?!Shared(?![A-Za-z0-9._-]))"
    r"[A-Za-z0-9._-]{1,64}"
)
# Share-only secret rules. Each keeps the key and replaces only the value, so
# the page still says what kind of value was there.
_QUOTE = r"[\"'\\]{0,3}"
_QUOTE1 = r"[\"'\\]{1,3}"
# A value is one token; it ends at a quote, whitespace or a delimiter. The
# renderer scrubs one string up to three times (leaf, dump, page), so an
# already redacted value must not match again (`x=[REDACTED]` -> `x=[REDACTED]]`).
_VALUE = r"(?P<v>(?!\[REDACTED\])[^\s\"'\\,;&}{)\]]{4,4096})"
# A bare `_key` suffix would swallow primary_key / foreign_key / sort_key, so
# the key-name families that hold a secret are enumerated instead. The name may
# end only as the key itself (plural, or an encoding of its value): any other
# suffix names another thing (encryption_key_path, account_key_prefix).
_KEY_FAMILIES = (
    r"(?:encryption|master|signing|jwt|hmac|auth|account)[_-]?key"
    r"s?(?:[_-](?:base64|b64|hex|value|secret))?"
)
# Session ids are secrets (a stolen one is a login), but a bare `session` is an
# ordinary word and variable (`session = requests.Session()`).
_SESSION_ID = r"(?:session[_-]?id|sessid)"
# Words that are secret names only when they are the whole name: `pass`,
# `pw`, `sig`, `signature`. As the keyword table's substring-plus-suffix match
# they would hit passed=True, bypass, signal, design.
_SHORT_NAME = r"(?:pass|pw|sig|signature)"
_WHOLE_WORD = r"(?<![A-Za-z0-9])" + _SHORT_NAME + r"(?![A-Za-z0-9])"


def _name_forms(name: str, *, whole_word: bool, compound: bool) -> str:
    """The key part of a rule for a name that is also an ordinary word: the
    name, a separator, and only the forms that prose does not produce (no space
    before ``=`` unless the value is quoted). ``whole_word`` refuses a longer
    word around the name (bypass); ``compound`` also takes an unquoted
    ``DB_PASS: x``, which a type annotation (``smtp_pass: Optional[str]``) can
    imitate."""
    lead = r"(?<![A-Za-z0-9])" if whole_word else ""
    forms = [
        # pass=x  --pw=x  X-Amz-Signature=x
        lead + name + _QUOTE + r"=\s{0,3}" + _QUOTE,
        # pass = 'x': spaces around "=" only when the value is quoted, which
        # keeps `tests pass = 12` out
        lead + name + r"\s{1,3}=\s{0,3}" + _QUOTE1,
        # {"pass": "x"}: a quoted name
        r"(?<=[\"'\\])" + name + _QUOTE1 + r":\s{0,3}" + _QUOTE,
        # pass: 'x': a quoted value
        lead + name + _QUOTE + r":\s{0,3}" + _QUOTE1,
    ]
    if compound:
        # DB_PASS: x  X-Goog-Signature: x
        forms.append(r"(?<=[A-Za-z0-9][_-])" + name + _QUOTE + r":\s{0,3}" + _QUOTE)
    return "|".join(forms)


# Where an unquoted value ends.
_TOKEN_END = r"(?=[\s\"'\\,;&}{)\]]|\Z)"
_IDENT = r"[A-Za-z_][A-Za-z0-9_]{0,63}"
# Source code, not a value: `signature_bytes` (words and underscores only, so a
# digit makes it a password again), `request.session_id`, `getpass()`.
_CODE_EXPRESSION = (
    r"(?:[A-Za-z]{1,64}(?:_[A-Za-z]{1,64}){1,8}"
    + _TOKEN_END
    + r"|"
    + _IDENT
    + r"(?:\."
    + _IDENT
    + r"){1,8}(?:"
    + _TOKEN_END
    + r"|\()|"
    + _IDENT
    + r"(?:\."
    + _IDENT
    + r"){0,8}\()"
)
# What follows a short name and is not a secret: a flag or placeholder
# (pass=True, sig=None), a count (pass=1200; six digits and more stay secret),
# a type (smtp_pass: bytes, smtp_pass: Optional[str]), and, when the value is
# not quoted, an identifier or call (sig=signature_bytes).
_NOT_A_VALUE = (
    r"(?!(?:true|false|null|none|nil|undefined)(?![A-Za-z0-9_])"
    r"|[0-9]{1,5}" + _TOKEN_END + r"|(?:optional|list|dict|set|frozenset|tuple|union"
    r"|literal|sequence|mapping|iterable|callable|type|annotated|classvar|final)\["
    r"|(?:str|string|int|integer|float|bool|boolean|bytes|bytearray|uuid|number"
    r"|secretstr)" + _TOKEN_END + r")"
    r"(?:(?<=[\"'\\])|(?!" + _CODE_EXPRESSION + r"))"
)
_SHARE_SECRETS = [
    re.compile(
        r"(?i)(?P<k>(?:proxy-)?authorization"
        + _QUOTE
        + r"\s{0,3}[:=]\s{0,3}"
        + _QUOTE
        + r"(?:bearer|basic|token|digest|negotiate)\s{1,3})"
        r"(?P<v>[A-Za-z0-9._~+/=\-]{6,4096})"
    ),
    re.compile(
        r"(?i)(?P<k>(?:set-)?cookie" + _QUOTE + r"\s{0,3}[:=]\s{0,3}" + _QUOTE + r")"
        r"(?P<v>[^\r\n\"'\\]{4,4096})"
    ),
    # {"name": "DB_PASSWORD", "value": "..."} (Kubernetes env, ECS task
    # definitions, GitHub Actions variables). The page dumps JSON with
    # indent=2, so a newline and a deep indent can sit between the two fields:
    # the gap is bounded by 8192, not by the 3 spaces of a compact dump
    # (json.dumps itself stops near 1200 levels: about 2400 spaces at indent=2).
    re.compile(
        r"(?i)(?P<k>\"name\"\s{0,3}:\s{0,3}\"[^\"\n]{0,80}"
        r"(?:passw|secret|token|key|credential|" + _SESSION_ID + "|" + _WHOLE_WORD + ")"
        r"[^\"\n]{0,80}\"\s{0,3},\s{0,8192}"
        r"\"value\"\s{0,3}:\s{0,3}\")(?P<v>[^\"\n]{1,4096})"
    ),
    re.compile(
        r"(?i)(?P<k>(?:passw(?:or)?d|passwd|pwd|secret|token|api[_-]?key|apikey"
        r"|access[_-]?key|private[_-]?key|credential)s?[\w.-]{0,40}"
        + _QUOTE
        + r"\s{0,3}[:=]\s{0,3}"
        + _QUOTE
        + r")"
        + _VALUE
    ),
    re.compile(
        r"(?i)(?P<k>"
        + _KEY_FAMILIES
        + _QUOTE
        + r"\s{0,3}[:=]\s{0,3}"
        + _QUOTE
        + r")"
        + _VALUE
    ),
    # Session ids (JSESSIONID, PHPSESSID, session_id=...). Never a bare
    # `session`. An unquoted `session_id: x` is not taken either, as a type
    # annotation (`session_id: Optional[str]`) looks just like it; only the
    # upper-case cookie / env names and the hyphenated header are.
    re.compile(
        r"(?i)(?P<k>"
        + _name_forms(_SESSION_ID, whole_word=False, compound=False)
        + r"|(?-i:SESSION[_-]?ID|SESSID)"
        + _QUOTE
        + r":\s{0,3}"
        + _QUOTE
        + r"|session-id"
        + _QUOTE
        + r":\s{0,3}"
        + _QUOTE
        + r")"
        + _NOT_A_VALUE
        + _VALUE
    ),
    # pass / pw / sig / signature. `--- PASS: TestFoo` and `Signature: Sent from
    # my phone` stay; so do `pass=True`, `pass=1200` and `sig=signature_bytes`
    # (see _NOT_A_VALUE).
    re.compile(
        r"(?i)(?P<k>"
        + _name_forms(_SHORT_NAME, whole_word=True, compound=True)
        + r")"
        + _NOT_A_VALUE
        + _VALUE
    ),
]


class RedactionUnavailable(RuntimeError):
    """The secret scanners could not be loaded; share output is refused."""


def load_secret_scanners(
    hooks_dir: Path | None = None,
) -> list[tuple[str, re.Pattern[str]]]:
    # .claude/skills/session-dossier/this.py -> .claude/hooks. resolve() follows
    # the ~/.claude/skills symlink back into the repository.
    directory = hooks_dir or Path(__file__).resolve().parents[2] / "hooks"
    path = directory / "_bash_review_common.py"
    spec = importlib.util.spec_from_file_location("_dossier_secret_scanners", path)
    if not path.is_file() or spec is None or spec.loader is None:
        raise RedactionUnavailable(f"シークレット検出パターンが見つかりません: {path}")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except (ImportError, OSError, SyntaxError) as exc:
        raise RedactionUnavailable(
            f"シークレット検出パターンを読み込めません: {exc}"
        ) from exc
    scanners: Any = getattr(module, "_SECRET_SCANNERS", None)
    if (
        not isinstance(scanners, list)
        or not scanners
        or not all(
            isinstance(item, tuple)
            and len(item) == 2
            and isinstance(item[1], re.Pattern)
            for item in scanners
        )
    ):
        raise RedactionUnavailable("シークレット検出パターンの形式が想定と違います")
    return scanners


def _redact_key_blocks(text: str) -> tuple[str, int]:
    """Remove each BEGIN..END private-key block (END within 64 KiB)."""
    if "PRIVATE KEY" not in text:
        return text, 0
    ends = [m for m in _PRIVATE_KEY_END.finditer(text)]
    if not ends:
        return text, 0
    starts = [m.start() for m in ends]
    parts: list[str] = []
    last = 0
    count = 0
    for begin in _PRIVATE_KEY_BEGIN.finditer(text):
        if begin.start() < last:
            continue
        k = bisect.bisect_left(starts, begin.end())
        if k == len(ends) or ends[k].start() - begin.end() > _KEY_BLOCK_SPAN:
            continue
        parts.append(text[last : begin.start()])
        parts.append(REDACTED)
        last = ends[k].end()
        count += 1
    if not count:
        return text, 0
    parts.append(text[last:])
    return "".join(parts), count


def _line_break_at(text: str, i: int) -> int:
    """Length of the line break starting at i (real or JSON-escaped), or 0."""
    for brk in ("\r\n", "\n", "\\r\\n", "\\n"):
        if text.startswith(brk, i):
            return len(brk)
    return 0


def _redact_key_heads(text: str) -> tuple[str, int]:
    """Remove each BEGIN line left without its END, with the base64 lines that
    follow it and nothing after them (a blank line or other text stops it)."""
    parts: list[str] = []
    last = 0
    count = 0
    for match in _PRIVATE_KEY_BEGIN.finditer(text):
        if match.start() < last:
            continue
        end = match.end()
        while True:
            brk = _line_break_at(text, end)
            run = end + brk
            while run < len(text) and text[run] in _BASE64_CHARS:
                run += 1
            if brk == 0 or run == end + brk:
                break
            end = run
        parts.append(text[last : match.start()])
        parts.append(REDACTED)
        last = end
        count += 1
    if not count:
        return text, 0
    parts.append(text[last:])
    return "".join(parts), count


def _redact_key_tails(text: str) -> tuple[str, int]:
    """Remove each orphan END line with the base64 body right before it."""
    parts: list[str] = []
    last = 0
    count = 0
    for match in _PRIVATE_KEY_END.finditer(text):
        start = match.start()
        while start > last and text[start - 1] in _KEY_BODY_CHARS:
            start -= 1
        # Keep one separating space or newline before the redaction.
        while start < match.start() and text[start] in "\r\n\\":
            start += 1
        parts.append(text[last:start])
        parts.append(REDACTED)
        last = match.end()
        count += 1
    if not count:
        return text, 0
    parts.append(text[last:])
    return "".join(parts), count


def _redact_emails(text: str) -> tuple[str, int]:
    """Replace e-mail addresses, extending each match back over a local part
    longer than the regex's 64-character window, in one linear pass."""
    parts: list[str] = []
    last = 0
    count = 0
    for match in _EMAIL.finditer(text):
        start = match.start()
        while start > last and text[start - 1] in _EMAIL_LOCAL_CHARS:
            start -= 1
        parts.append(text[last:start])
        parts.append(REDACTED)
        last = match.end()
        count += 1
    if not count:
        return text, 0
    parts.append(text[last:])
    return "".join(parts), count


class Redactor:
    def __init__(
        self,
        *,
        home: str,
        authors: list[str],
        scanners: list[tuple[str, re.Pattern[str]]] | None = None,
    ) -> None:
        self.scanners = scanners if scanners is not None else load_secret_scanners()
        self.excluded: Counter[str] = Counter()
        self.replaced: Counter[str] = Counter()
        home = home.rstrip("/")
        slug = re.sub(r"[^A-Za-z0-9]", "-", home)
        self._rules: list[tuple[str, re.Pattern[str], str]] = []
        if home:
            # macOS paths are case-insensitive: /Users/NAME is the same place.
            self._rules.append(
                (
                    "home",
                    re.compile(re.escape(home) + r"(?![A-Za-z0-9_.-])", re.I),
                    "~",
                )
            )
            self._rules.append(
                (
                    "home_slug",
                    re.compile(re.escape(slug) + r"(?![A-Za-z0-9_.])", re.I),
                    "~",
                )
            )
        # Anyone else's home directory names a person too.
        self._rules.append(("home", _OTHER_HOME, r"/\1/[user]"))
        # The bare account name also turns up outside the home path (temp dirs
        # such as pytest-of-<user>, URLs), percent-encoded or after an escaped
        # newline. Too short a name would match ordinary words.
        user = Path(home).name if home else ""
        if len(user) >= _MIN_USERNAME_LEN:
            self._rules.append(
                (
                    "username",
                    re.compile(
                        r"(?:(?<=%2F)|(?<=%2f)|(?<=\\n)|(?<=\\t)|(?<![A-Za-z0-9]))"
                        + re.escape(user)
                        + r"(?![A-Za-z0-9])",
                        re.I,
                    ),
                    "[user]",
                )
            )
        names = sorted(
            {a.strip() for a in authors if len(a.strip()) >= _MIN_AUTHOR_LEN},
            key=len,
            reverse=True,
        )
        if names:
            pattern = "|".join(re.escape(n) for n in names)
            self._rules.append(
                (
                    "git_author",
                    re.compile(rf"(?<!\w)(?:{pattern})(?!\w)", re.I),
                    "[author]",
                )
            )

    def _sub(
        self, category: str, pattern: re.Pattern[str], repl: str, text: str, count: bool
    ) -> str:
        new, n = pattern.subn(repl, text)
        if count and n:
            self.replaced[category] += n
        return new

    def _share_secret(self, pattern: re.Pattern[str], text: str, count: bool) -> str:
        """Keep the key, redact the value; a bare number (max_tokens: 4096) is
        a setting, not a secret, and stays."""
        changed = 0

        def repl(match: re.Match[str]) -> str:
            nonlocal changed
            # Only a token *count* (max_tokens: 4096) is a setting; a numeric
            # password or PIN is still a secret.
            if match.group("v").isdigit() and match.group("k").lower().startswith(
                "tokens"
            ):
                return match.group(0)
            changed += 1
            return match.group("k") + REDACTED

        text = pattern.sub(repl, text)
        if count and changed:
            self.replaced["secret"] += changed
        return text

    def scrub(self, text: str, *, count: bool = True) -> str:
        text = self._sub("system_reminder", _REMINDER, "", text, count)
        for walk in (_redact_key_blocks, _redact_key_heads, _redact_key_tails):
            text, n = walk(text)
            if count and n:
                self.replaced["secret"] += n
        for _label, pattern in self.scanners:
            text = self._sub("secret", pattern, REDACTED, text, count)
        for pattern in _SHARE_SECRETS:
            text = self._share_secret(pattern, text, count)
        text, n = _redact_emails(text)
        if count and n:
            self.replaced["email"] += n
        text = self._sub("tmp_dir", _TMP_DIR, "<tmp>", text, count)
        for category, pattern, repl in self._rules:
            text = self._sub(category, pattern, repl, text, count)
        return text

    def exclude(self, category: str, n: int = 1) -> None:
        if category not in EXCLUDE_LABELS:
            raise KeyError(category)
        self.excluded[category] += n

    def summary(self) -> list[tuple[str, str, int]]:
        """Every category, zeros included, in a fixed order."""
        return [
            *(
                ("除外", label, self.excluded[key])
                for key, label in EXCLUDE_LABELS.items()
            ),
            *(
                ("置換", label, self.replaced[key])
                for key, label in REPLACE_LABELS.items()
            ),
        ]
