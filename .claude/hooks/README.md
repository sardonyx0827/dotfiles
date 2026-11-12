# Hooks System

## Hook Types

- **PreToolUse**: Before tool execution (validation, parameter modification)
- **PostToolUse**: After tool execution (auto-format, checks)
- **Stop**: When session ends (final verification)

## Current Hooks (in ~/.claude/settings.json)

### PreToolUse

- **bash-review** (`hooks/bash-review.py` via `hooks/bash-review-launcher.sh`,
  matcher: `Bash`):
  Reviews every Bash command before execution — static allow/deny fast-paths
  (no AI call), a static secret pre-send scan that refuses to forward commands
  carrying raw credentials to any LLM, a parallel Gemini+Codex AND-gate for
  high-risk commands, and a Gemini→Codex cascade for the low-risk majority.
  Logs to `~/.claude/logs/bash-review.log` and `/tmp/claude_hooks/logs/`
  (directory 0700, files 0600). The summary log holds one decision per
  physical line: a newline in the command or the reason is written as `⏎`,
  and a `|` in the reason as `/`, so a multi-line command cannot plant a
  line that reads as a second decision (the `bash-review-log` mod applies the
  same `⏎` when it matches a summary line to its detail log).
  `settings.json` launches it through the launcher, not bare `python3`: a hook
  command that cannot start (no `python3`, missing file) or crashes with an
  unexpected exit code is treated by Claude Code as a _non-blocking_ error —
  the command would run unreviewed. The launcher converts every such
  "review never happened" case into an explicit `ask`, and passes the normal
  vocabulary (exit 0 + decision JSON, exit 2 + stderr) through untouched.
  See **bash-review — design rationale & threat model** below for why the
  tiers are structured this way and what they do (and do not) defend against.
- **git-push-review** (`hooks/git-push-review.sh`, matcher: `Bash`,
  no `if` filter — deliberately unconditional):
  Detects `git push` commands and forces a confirmation prompt
  (permissionDecision: ask) with a summary of the branch, commits,
  and diffstat about to be pushed. Enforces the pre-push review step
  of `rules/git-workflow.md`.
  The summary is taken from the repository the push will actually run in:
  `git -C <dir>` and a literal `cd <dir>` ahead of the push both retarget it.
  When that directory cannot be determined for certain (a variable, `cd -`,
  `~user`, `pushd` / `popd`, a subshell, a heredoc (`<<`) or comment (`#`)
  in a command that also has a `cd`, several `cd`s, a `cd` with options or
  extra operands, a `..` component, a quoted `~`, a target that is not an
  existing directory, a control character in the command other than tab /
  newline (DEL included) or any control character in the `cd` target or the
  `-C` value, `--git-dir` /
  `--work-tree` / `GIT_DIR` / `GIT_WORK_TREE` anywhere in it, or a plain
  relative `cd` while `CDPATH` is set in the hook's environment or in the
  command itself), the prompt
  carries a note instead of a summary — a summary of the wrong repository
  is worse than none. Only the push's own `-C` picks the repository; a `-C`
  on another git call in the same command does not.
  Ahead of the summary the prompt names the push target (remote and
  refspec), and a `WARNING` line calls out `--force` / `-f` /
  `--force-with-lease` / `--force-if-includes` / a `+` refspec / `--delete` /
  `-d` / a `:dst` refspec / `--mirror` / `--all` / `--tags` / `--prune`. When
  the refspec source is not the current branch, or `--all` and the like change
  which refs are sent, a note says the commit list below may not be what is
  actually pushed. When the arguments cannot be read reliably (inside `eval` /
  `sh -c` / `watch` / `--exec`, a refspec with an expansion, an unknown option,
  over 4000 characters, a control character) it does not guess and shows a
  note instead. URL userinfo is masked.
  A push that is not spelled `git … push` is confirmed too, with a note in
  place of the summary (the summary describes "the current branch to its
  upstream", which is not what these send): `git subtree push`, the transport
  `git push` itself runs (`git send-pack`, `git http-push`), and an alias —
  `git -c alias.p=push p`, or `git config alias.ps push`, the one moment the
  hook can see a later `git ps`. Alias values are not interpreted: `-c alias.`
  or `config … alias.` in the command as the shell reads it (quoted text such
  as a commit message does not count; a backslash spelling such as
  `git conf\ig` does), with the letters `push` anywhere in the command, is
  enough — so `alias.lg 'log --grep=push'` asks as well.
  Past the 4000-character cap the quote scan is replaced by an
  over-approximation; it also drops backslashes and folds `\`-newline, so
  `git pu\sh` or a continuation between `git` and `push` behind a long heredoc
  is still seen.
  On detection: a stray apostrophe inside a comment (word-initial `#`), a
  heredoc body or `$'...'` can no longer open a false quote and hide a push on
  a later line. A `<<` with no terminator line (an arithmetic shift such as
  `$((1<<2))`) is not taken for a heredoc, and a heredoc body that is code
  folds `\`-newline continuations as the shell does. A quoted word with no
  whitespace (`git "push"`, `"git" push`) counts as one word, also after
  `then` / `{` / `!` or behind a wrapper such as `timeout 5`, and a push inside
  the string that `git submodule
foreach`, `git rebase --exec` (`-x`) or `watch` runs is detected too
  (`submodule foreach` runs in each submodule, so it shows a note instead of a
  summary). The same holds for a string a shell runs however it is invoked:
  options before `-c` (`bash -euo pipefail -c`, `-o pipefail`, `-O extglob`,
  `+e`), `c` inside a cluster (`-cl`, `-ce`), code piped or here-stringed into
  a shell (`… | sh`, `bash <<< '…'`), `$SHELL -c`, `fish` / `csh` / `tcsh` /
  `ash` / `mksh`, and a `\`-newline continuation inside that string. A global
  option value with a backslash-escaped space (`git -C ~/My\ Project push`,
  `git -c user.name=My\ Name push`) no longer ends the value early and hides
  the push. When the command carries a `-C` the hook cannot tie to the push
  (the push sits behind a wrapper such as `timeout 5` / `env X=1` / `nice`, or
  inside a `bash -c` string), or two pushes name different `-C` directories,
  the prompt shows the "target repository could not be determined" note rather
  than a summary of the hook's own cwd.
  This entry used to carry `if: Bash(git push*)`. That filter prefix-matches
  each sub-command, so it never fired for the very forms this script exists
  to catch — `git -C <dir> push`, `git --no-pager push`, `git -c k=v push`,
  `eval "git push …"`, or an expansion among git's options such as
  `git $OPTS push` (it could expand to any option, so it counts as a push)
  — leaving the script's own detection unreachable in
  production while `permissions.allow` (`Bash(git:*)`, defaultMode `auto`)
  let those forms through with no confirmation at all. The Claude Code docs
  are explicit: "Because the filter is best-effort, use the permission system
  rather than a hook to enforce a hard allow or deny."
  Per that advice `permissions.ask` now carries `Bash(git push:*)` as a
  backstop for when this hook fails (it is fail-open by design, and Claude
  Code treats a crashed or timed-out hook as non-blocking). That rule
  prefix-matches exactly like `if` did, so it covers only the plain form —
  the two layers are complementary, not redundant: the hook covers every
  form while healthy, the permission rule covers the common form regardless.
  Running on every Bash call costs a flat ~70 ms rather than scaling with
  command length: the script short-circuits before the O(n^2) quote-stripping
  state machine, on a grep of the jq-decoded command with quotes, backslashes
  and newlines removed. That probe must run on the DECODED command — the same
  `tr` over the raw stdin turns JSON's `\n` escape into a stray `n`, which
  splits `git pu\<newline>sh` into `git punsh` and reopens the line-continuation
  bypass 8f2d386 closed.
  `tests/test_config_wiring.py::test_pretooluse_bash_hooks_are_unconditional`
  keeps the filter from coming back.

### PostToolUse

Matcher: `Write|Edit|MultiEdit`. One handler,
**format-then-lint** (`hooks/format-then-lint.sh`), which runs in order:

1. **auto-format** (`hooks/auto-format.sh`):
   Extracts the edited file path from hook JSON and runs the matching
   formatter (prettier, etc.). Logs to `~/.claude/logs/format.log`.
2. **lint** (`hooks/lint.sh`):
   Static analysis after formatting. Exits with code 2 to feed errors
   back to Claude and trigger self-correction. Logs to
   `~/.claude/logs/lint.log`.

The wrapper exists because the order is load-bearing and listing the two as
separate handlers does not buy it: Claude Code runs every handler matching an
event in parallel ("Since hooks run in parallel, the order is
non-deterministic"). When lint won that race it reported findings the
formatter owns — ruff's import sort, rubocop's `Layout` — as exit 2, costing a
hand-fix turn for work about to happen anyway. The two cannot simply be joined
with `&&` either: both read the payload off stdin with `jq`, so the first
would consume it and the second would find no file path and return 0, quietly
removing the gate. `format-then-lint.sh` spools the payload to a temp file,
feeds both, and lets lint's exit code be the hook's.
`tests/test_format_then_lint.py` pins all of that.

### Stop

- **stop-audit** (`hooks/stop-audit.sh`):
  Final verification gate when Claude finishes a turn. Scans modified
  and untracked files for leftover debug statements (`console.log` /
  `debugger` in JS/TS, `breakpoint()` / `pdb.set_trace()` in Python)
  and blocks once with the findings so Claude removes them. Before the
  first commit (no `HEAD` yet) it scans the staged files instead of the
  diff against `HEAD`.
  The report is bounded — 5 matches per file, 200 bytes per matched line
  (cut on a character boundary), 20 files and a count of the rest — because a
  single minified line used to stall the hook for minutes under macOS's
  bash 3.2, and past about 1 MB made it print nothing and let Stop through.
  `stop_hook_active` guards against infinite loops.
  Skipped entirely when `EDITOR_AI_ONESHOT` is set — the marker the vim /
  nvim AI features put on their `claude -p` and `codex exec` calls. Those are
  generation-only (commit message, buffer check, hints) and audit the user's
  own uncommitted work rather than anything the agent wrote, so a block there
  made the agent edit the working tree and return "removed the debug
  statement" in place of the generated text. bash-review sets the same marker
  on its own nested `codex exec` review call, so the Codex-side Stop hooks
  skip that run too.

## Shared libraries (`_`-prefixed, not hooks themselves)

These four files hold the per-file logic shared between the two sides — the
lint/format matrices and bash-review's verdict logic. They live here as the
_only_ copies: `.codex/hooks/` carries neither a duplicate nor a link, and its
entry points resolve `../../.claude/hooks` from their own **physical** location
(`os.path.realpath(__file__)` in Python, `cd -P` in shell) and load these files
from there. One file means the shared logic cannot drift apart, and a
symlink-free subtree means a `core.symlinks=false` checkout — Git for Windows'
default, which materialises a symlink as a text file holding its target path —
gets the same working tree as everyone else. (An earlier design used relative
symlinks; it had the same anti-drift property but broke exactly that checkout.)

Physical resolution is the load-bearing part: `install.sh` links
`~/.codex/hooks` → `<repo>/.codex/hooks`, so a _logical_ `../../.claude/hooks`
would land on `~/.claude/hooks` and resolve only by the coincidence that
`install.sh` links that path too. `tests/test_hook_sync.py` pins all of it.

The hooks that consume this logic (`lint.sh`, `auto-format.sh`,
`bash-review.py`, …) are _not_ shared: each side keeps its own copy, because
Claude and Codex differ in how they receive targets, report verdicts, and place
hooks on events. See `.codex/hooks/README.md` for the full table and those
differences.

- **`_hook_common.sh`**: `hook_log` (timestamped, size-capped),
  `hook_notify` (terminal-notifier / osascript / notify-send), and
  `hook_find_nearest_bin` (nearest `node_modules/.bin` lookup used by lint and
  format).
- **`_lint_common.sh`**: `hook_lint_file` — the per-language lint matrix.
- **`_format_common.sh`**: `hook_format_file` — the per-language formatter matrix.
- **`_bash_review_common.py`**: bash-review's verdict logic and review calls.

Contract for the shell ones: they define functions and nothing else. Sourcing
must not print, `mkdir`, or touch `set`/`IFS`/`trap`/cwd — the Codex `lint.sh`
sources them before `exec 1>/dev/null`, so any output at source time would land
in the structured-output channel Codex parses and fail the hook. Function names
are namespaced `hook_` because bash is dynamically scoped and a collision with a
caller's local fails silently. Variables inside them are not prefixed
(`FILE_PATH`, `EXTENSION`, …), so callers must not rely on names they share.

## bash-review — design rationale & threat model

`bash-review` is a **guardrail against an over-eager agent**, not an adversarial
security boundary. Its goal is to stop _the assistant_ from running something
destructive or secret-leaking — not to withstand a human who is actively trying
to defeat it. `permissions.deny` in `settings.json` (`rm -rf`, `sudo`,
`curl`/`wget`, reads of `*.key` / `.env`, …) sits underneath it.

Do not read that layering as "the hook is advisory because `permissions.deny` is
the hard boundary." `permissions.deny` matches on the command prefix, so shell
grammar defeats it: a denied command is refused when written plainly and runs
when wrapped in a subshell (verified with a harmless probe — bare `rm -r <path>`
refused, `( rm -r <path> )` executed). Neither layer is a boundary an adversary
cannot cross; they are two independent chances to catch a mistake, which is why
this hook resolves obfuscated spellings itself rather than deferring.

Given that model, the flow is tuned by **how dangerous the command is**, keeping
latency low for the common case:

1. **Fast pre-block / safe-skip (no AI call).** Dangerous executables (`curl`,
   `nc`/`netcat`/`ncat`, `scp`/`sftp`/`telnet`/`socat`/`ftp`/`slogin`, `dd`,
   `sudo`, …) are denied outright and obvious
   read-only commands (`ls`, `cat`, `git status`, …) are allowed — both without
   any API/CLI round-trip.

   The two sides are not symmetric. Denial **resolves the executable** rather
   than matching a literal prefix, so `/usr/bin/curl`, `CURL`, `c"u"rl`,
   `env curl`, `(curl …)`, a `noglob` / `nocorrect` / `builtin` / `lockf` /
   `sandbox-exec` / `xcrun` prefix, a `FOO+=x` prefix and
   `function f { curl …; }` all resolve to `curl` and are denied. What it
   cannot resolve — `$(which curl)`, `${CURL}`, `eval "…"`, `sh -c "…"`, an
   executable with an unquoted glob (`/usr/bin/cur?`) or a brace expansion
   (`{curl,x}`, `c{u,}rl`, which the shell rewrites into `curl x` / `curl crl`
   before running it; a heredoc body line such as `**Why**:` or compact JSON in
   a commit message is data, not an executable, unless the command contains a
   form the parser gives up on, such as `$((1<<2))`, which could fake a body) —
   is not waved through either: those escalate to the high-risk gate below (step 3:
   it runs only when both models return `ALLOW`; anything short of that stops
   for confirmation or is denied).

   Denial looks only at text the shell actually runs. Every sub-command is also
   checked line by line (so `ls\nsudo …` is caught), and that line split used
   to ignore quoting, so a commit-message line, heredoc body line or
   multi-line quoted argument that merely began with `ssh` / `curl` / `sudo`
   was hard-denied — including Claude Code's own
   `git commit -m "$(cat <<'EOF' …)"`. Such a match is now dropped when (a) it
   lies only in a heredoc body or a quoted span containing a newline, (b) no
   `$(…)` / backtick in that text runs it, and (c) every command that does run
   is on a small data-only allowlist (`git` commit/add/push/…, `gh pr|issue|release`,
   `cat`, `echo`, `printf`, `cd`). It is an allowlist on purpose: the
   forms that execute a string (`bash -c`, `eval`, `| sh`, `git rebase --exec`,
   `git -c alias.x=!…`, `tmux send-keys`, `$SHELL -c`, …) cannot be enumerated.
   A line is judged by its raw first word, never through wrapper stripping
   (`watch echo "…"` runs its arguments through `sh -c`) and never past a
   leading `VAR=value` (`GIT_EDITOR` / `GIT_SSH_COMMAND` run their value); a
   path (`./cat`) is not the allowlisted command; git options that start a
   program (`-c`, `--config-env`, `--receive-pack`, `--upload-pack`, `--exec`,
   `--exec-path`, including the abbreviations git accepts) void the exemption,
   as do `(` `)` `` ` `` `{` `}` (zsh glob qualifiers such as `*(e:…:)` run code)
   and any `>` write — writing a file can plant code that git's hooks or a later
   safe-skipped command runs, so `cat > f <<'EOF'` keeps the deny. Commands with
   `((`, `$[` or a `<<` inside `${…}` are not parsed at all (an arithmetic or
   expansion `1<<X` would otherwise be read as a heredoc that swallows the next
   lines), nor is a `<<$'EOF'` delimiter.
   A python heredoc whose body starts a line with a denied name (`su = 3`) is a
   known remaining false positive.

   Right after denial, every `codex` invocation is checked against an
   **allowlist** of the shapes the codex-consultation / codex-image-gen skills
   use, and anything outside it is an `ask` (Codex variant: block) before any
   model is consulted (`codex_flag_ask_reason`): `-s read-only`, or
   `-s workspace-write` only with a `-C` that resolves (realpath) inside a
   session scratchpad, `/tmp/claude-<uid>/…`;
   `-c sandbox_mode=read-only` / `-c model_reasoning_effort=…` only (values
   decoded as TOML, so escapes do not hide `danger-full-access`); the flags
   `-C -o -m -i --json --ephemeral --skip-git-repo-check`; subcommands
   `exec` / `exec resume` / `features list`; no environment assignment but
   `EDITOR_AI_ONESHOT` (so not `CODEX_HOME=`); no expansion (`$`, backtick, or a
   brace such as `{x,-s}` that the shell turns into extra words) in an argument
   or redirect target; a heredoc / here-string prompt only when nothing in the
   command expands; and a prompt read from a file on stdin only when the command
   runs `secret_scan.py < <that file>` at a command position, chained with `&&`
   straight into this `codex` (not after it, not across `||` or `;`, not inside
   quotes) — a prompt written with the Write tool is otherwise never scanned. Glued short options (`-pevil`, `-cKEY=V`, `-c=K=V`)
   are normalised first. Like denial, lines inside heredoc bodies and multi-line
   quoted strings are not read, so a commit message that mentions a flag does
   not trip it. Three things hold wherever they appear in the invocation:
   the stdin rules apply even when a prompt is also given on argv
   (`codex exec 'review' < prompt.md`, `cat prompt.md | codex exec 'review'`),
   and each is applied on its own — every `< FILE` must have been scanned
   (`/dev/null` included: the hook cannot tell a real redirect from a quoted
   argument that looks like one), a heredoc is checked whatever else is
   redirected, a redirect on another descriptor (`3< file`) is an input file
   but does not make stdin "redirected", and stdin taken from a descriptor
   (`0<&3`, `0>&3`) asks because its source cannot be traced; a `--` ends flag
   parsing but not the expansion and redirect
   checks (`codex exec -- "$(cat f)"` asks); and the environment may not be
   changed by another command in the same line either — `export` / `declare` /
   `typeset` assigning anything but `EDITOR_AI_ONESHOT` (also behind `command`
   / `builtin`), a bare `NAME=value` command, `set -a`, or `eval` / `source` /
   `.`, whose effect the hook cannot read (`export CODEX_HOME=/x; codex exec
…`). Printing forms such as `declare -p PATH` do not count. That last check
   does not follow order or subshells, so an `export` after the `codex` call
   asks too.
   The reason names a flag, subcommand, config key or variable only when it
   is a plain word (letters, `_`, `.`, `-`); any other token is withheld, and
   when the secret scan found a credential in the command the whole reason is
   one fixed sentence — a reason is shown in the prompt and written to the
   summary log, which the command-field redaction does not cover. Claude reaches Codex through `codex exec` on the Bash tool since
   Codex CLI 0.154 removed its MCP-server mode; a denylist of risky flags kept
   missing spellings, which is why this is an allowlist. Prefix rules in
   `permissions.ask` back up the common risky spellings for when the hook is
   down; they cannot see a flag placed later in the command.

   Safe-skip is the conservative side: it matches the raw command string against
   an allowlist, so anything wrapped in grammar simply fails to match and goes
   to review. The resolver is consulted only by the exclusions below (grep / rg
   / tmux / output-file flags), and only to send more commands to review. A sensitive path (`.env`, `id_rsa`,
   `.ssh`, …) forces review even when the prefix looks safe. So do the reads
   that reach a secret without naming it, since such a command names no file
   and the `permissions.deny` Read rules cannot catch it either:
   - an unquoted glob, brace or parenthesis (`cat .e*` expands to `.env`; the
     Bash tool's shell is zsh, where `.e(n)v` is a group glob and `=(cmd)`
     runs cmd). A quoted regex such as `grep 'a.*b'` stays fast.
   - a grep that may read a directory. Claude Code's Bash tool shadows grep
     with `ugrep --hidden`, which reads the hidden files under a directory
     operand even without `-r`, and treats `-3` as "recurse 3 levels". So a
     recursive flag, a digit flag, or an operand that does not look like a
     regular file (`.`, `src`, `sub/`) sends grep to review; a pipe
     (`… | grep foo`) or `grep foo file.txt` stays fast. Operands after `--`
     are read the same way (`grep -- PAT src` is reviewed): `--` ends the
     options, not the operands.
   - an rg that overrides its hidden/ignored-file skip: `--hidden`, `-.`, `-u`,
     `--unrestricted`, `--no-ignore*`, and also `-g` / `--glob` / `--iglob`
     (unless an `!` exclusion) and `-t` / `--type`, both of which read hidden
     files; or `-L` / `--follow`, which follows symlinks out of the tree.
   - anything else that turns a "read-only" command into execution, a write or
     an out-of-tree read: an absolute / `~` / `..` path, rg's
     `--pre` / `--pre-glob` / `--hostname-bin` / `-z` / `-f`, a tmux `#(…)`
     format or a `;`-chained second tmux command, and output-file flags
     (`--output` / `--outfile`, `tree -o`, and `tree -R`, which re-runs tree in
     every subdirectory with `-o 00Tree.html`).

2. **Secret pre-send scan (no external call).** Before any command is handed to
   an LLM, a static scan (`scan_secrets`) checks the command _and_ the full
   `tool_input` for raw credential **values** — known-format tokens (GitHub /
   AWS / Google / OpenAI / Slack (incl. app tokens and webhook URLs) / Stripe /
   npm / GitLab / Hugging Face / SendGrid / PyPI / Docker / DigitalOcean),
   PEM / OpenPGP private keys, JWTs,
   `Authorization: Bearer`/`Basic` headers, `user:pass@` URLs,
   credential-shaped assignments / long flags (`PGPASSWORD=…`, `--password …`,
   also with a prefix that names the credential: `--access-token …`,
   `--github-token …`, `--aws-secret-access-key …`, `--private-key …`; path-valued
   `--token-file` / `--password-file` and the negated `--no-password` are not
   values),
   and a secret-named key followed by its value in known setter CLIs
   (`aws configure set`, `vault kv put`, `gh secret set`, `heroku config:set`, …).
   `gh secret set` takes its value through `--body` / `-b`, so a literal of 8
   characters or more there counts whatever the secret is called (`--body
"$VAR"` is a reference, not a value; the floor is the one the other setter
   CLIs use, and keeps a placeholder such as `--body VALUE` in prose from
   stopping a commit message).
   References to a credential in code (`os.getenv(…)`, `os.environ[…]`,
   `self.token`) are not values and are not flagged.
   A known-format token counts even when it is glued to a `\n` / `\t` escape
   (`printf 'a\nghp_…'`, the escaped newlines of a JSON string, and the
   `\x0a` / `\u000a` / `\012` spellings) or to an
   underscore (`X=abc_ghp_…`): the start boundary is "no letter or digit
   before it, or one that belongs to such an escape", not `\b`, which saw
   `nghp_…` as one word. The session-dossier share redaction reuses this table.
   A hit fails closed to `ask` (Claude) / block (Codex) **without calling Gemini
   or Codex**, so a secret never leaves the machine for review; the
   reason / notification / logs carry only a generic category label, and the raw
   command is redacted out of the local audit logs (that redaction also applies
   when the same secret-bearing command is caught by the deny / safe-skip
   fast-paths above). This is **value-only** by design: sensitive _paths_
   (`cat ~/.aws/credentials`) reveal intent, not the secret itself, so they still
   go to normal review (step 1's sensitive-path rule). The threat model is
   _accidental_ secret inclusion by the agent, not deliberate obfuscated
   exfiltration — so detection is high-precision prefix / structural matching,
   not entropy or de-obfuscation.
3. **High-risk commands → parallel two-model AND-gate.** Commands that are
   dangerous _depending on context_ (`rm -r`, package installs like
   `pip install` / `npm i`, other shells' `-c` (csh / tcsh / fish / ash / pwsh),
   `osascript` / `bun` / `lua` inline eval, `deno eval`, remote runners
   (`npm exec`, `bunx`, `pipx run`, `uv tool run`), abbreviated destructive git
   options (`reset --har`, `clean --forc`, `push --forc`), …; see
   `high_risk_label`) are reviewed by Gemini
   **and** Codex run _in parallel_, and their verdicts are combined as an
   AND-gate (`combine_high_risk_verdicts`): `allow` only when **both** return
   `ALLOW`, `deny` only when **both** return `DENY`, and everything else —
   disagreement, `ASK`, or `ERROR` — resolves to `ask` with both verdicts
   attached. A single model's approval never auto-runs a high-risk command;
   convincing just one is not enough. Reserving both models for this
   dangerous-but-ambiguous minority is what keeps the AND-gate's latency cost off
   the common path.
4. **Low-risk commands → Gemini tier-1 cascade.** Everything else — the vast
   majority — is gated by a single call to a fast, cheap model. A Gemini `ALLOW`
   short-circuits and Codex is never called; that short-circuit is the main
   latency win. When Gemini returns `ASK` / `DENY` / `ERROR`, the command is
   escalated to the slower but more capable Codex, and how Codex's verdict is
   applied depends on whether Gemini's flag carried an _opinion_ — a lone
   `ALLOW` clears a no-opinion flag but **not** a flag that voiced caution:
   - Gemini **`DENY`** (an explicit refusal) or **`ASK`** (the model's explicit
     "a human should confirm this" — the review prompt defines `ASK` that way):
     a lone Codex `ALLOW` does **not** override it. The disagreement resolves to
     `ask`, put to the human with both verdicts attached. Letting one model's
     `ALLOW` clear the other's caution would turn the cascade into an OR-gate —
     convincing _either_ model would be enough to run the command, the opposite
     of what a second opinion is for — and would apply "fail toward the human"
     inconsistently across `ASK` vs `DENY`.
   - Gemini **`ERROR`** (unavailability — no opinion at all, not a verdict): a
     Codex `ALLOW` resolves it to `allow`. Codex is simply the sole reviewer
     left, so its approval carries (graceful degradation, no added friction).
   - Codex `DENY` → `deny`; Codex `ASK` → `ask`; Codex `ERROR` → fall back to
     Gemini's verdict (`deny` if Gemini said `DENY`, otherwise `ask`).

   How a reviewer's answer becomes a verdict (`_parse_verdict`): a line that
   starts with `DENY` or `ASK` counts, and `DENY` beats `ASK` beats `ALLOW`.
   `ALLOW` counts only when it stands alone on its line (closing quotes or
   markdown and one full stop may follow) — `ALLOW is not appropriate here: …`
   used to be read as an approval. No verdict line means `ask`.
   What Gemini's `ERROR` covers is deliberately narrow, because a lone Codex
   `ALLOW` resolves it: the key is missing or cannot be sent as a header
   value, or the call failed in transport (connection, TLS, an HTTP error
   status, a read cut short, a body that is not JSON) on both the primary and
   the fallback model. An
   answer that arrived but carries no usable verdict is an `ASK` instead, with
   the cause in the reason: a content-filter block
   (`promptFeedback.blockReason`, or any `finishReason` other than `STOP`), an
   answer cut at the output limit, a body that is not UTF-8, an empty body, or
   one with a part that is not text (the readable parts are not picked out of
   it). Whoever writes the command can trip a filter, so treating that as "no
   opinion" would leave only Codex to convince. All text parts of the answer
   are read, not just the first.

5. **Fail toward the human.** Malformed stdin, an unavailable Codex, or any
   exception raised before a decision is emitted resolves to `ask` / `deny`,
   never a silent allow. The reason then gives the exception's type and the
   hook line it came from, never its message: a message can quote anything
   (`http.client` quotes the header value it rejected, i.e. the API key).
   This includes the review **never starting**:
   `bash-review-launcher.sh` turns a missing `python3`, a missing
   `bash-review.py`, or a crash (any exit other than 0/2) into an explicit
   `ask`, where the bare `python3 …/bash-review.py` wiring would have been
   downgraded by Claude Code to a non-blocking error — i.e. fail-open. The
   review **never finishing** is covered too: the launcher's entry in
   `settings.json` carries `"timeout": 180` and `"onFailure": "block"`
   (Claude Code 2.1.295+), so a timeout — or the launcher itself failing to
   start — blocks the command instead of letting it run. 180 s sits above the
   review's own worst case (Gemini 30 s plus Codex 60 s). And input too large to
   analyse in that budget never reaches the analysis: a `tool_input` over
   32 KiB of JSON, or more than 32 command substitutions (`$(`, `<(`, `>(`, a
   backtick pair), resolves to `ask` (Codex: block) before step 1, with no
   external review and never `deny`. That bounds the quadratic cost of nested
   `$(`. `git-push-review.sh` stays fail-open (no `onFailure`), backed by
   `Bash(git push:*)` in `ask`.
   Should the hook layer be disabled altogether, `permissions.deny` still
   mirrors the hook's context-free hard-deny set — `sudo`/`su`/`doas`/`pkexec`,
   `ssh`/`slogin`/`scp`/`sftp`/`telnet`/`socat`/`ftp`/`curl`/`wget`/`nc`/`netcat`/`ncat`,
   `dd`/`shred`/`mkfs` — so none of them run
   unreviewed even with the hook down. The one residual: `permissions.deny`
   matches on word boundaries and cannot express the `mkfs.<fs>` family
   (`mkfs.ext4`, `mkfs.xfs`, …) in a single rule, so the common members are
   enumerated and exotic filesystems stay covered by the hook alone
   (`test_config_wiring.py` guards the mirror).

**Sandbox is off on purpose.** Claude Code's `sandbox` feature is disabled in
`settings.json` (`"sandbox": {"enabled": false}`): the enforcement boundary is
`permissions.deny` plus these hooks. While disabled, sibling keys under
`sandbox` (`network.allowedDomains`, `excludedCommands`) are never consulted —
they are inert, not an active allowlist, so don't read them as protection.

**Accepted tradeoffs (chosen, not overlooked):**

- On the **low-risk path**, a command that convinces the _single_ tier-1 model is
  allowed without a second opinion — accepted in exchange for latency. High-risk
  commands never get this treatment (step 3).
- LLM reviewers can be swayed by prompt-injection text inside the command; this
  layer is heuristic by nature, which is exactly why the enforced boundary lives
  in `permissions.deny`.
- The **secret pre-send scan** (step 2) is deliberately narrow: it matches
  credential _values_ by known format and a few structural shapes, tuned for
  _accidental_ inclusion. It does not defeat deliberate obfuscation (base64,
  quote-splitting, `$(…)`), does not catch very short opaque values (under 8
  chars for assignments and setter-CLI arguments, under 6 for `--password`-style
  long flags) or short-flag secrets (`-p<pw>`, which collide with `mkdir -p` etc.),
  and leaves sensitive _paths_ to normal review. A miss falls through to the
  usual AI review, not to a silent send of an unreviewed secret; a false
  positive only costs an `ask`.
- Safe-skipped `git status` / `git log` / `git diff` / … assume a **trusted** repository. Config-driven
  execution (`.git/config` `core.pager`, `[alias]`, `diff.external`) is out of
  scope — verifying it on every call would defeat the point of the skip. See the
  inline comments in `_bash_review_common.py`.

> The **low-risk cascade** (step 4) is single-model-with-short-circuit on purpose:
> don't "harden" _it_ into an AND-gate without re-checking the latency budget — a
> two-model review in front of _every_ Bash call would be too slow. The
> **high-risk tier** (step 3) already _is_ a parallel AND-gate; that is the whole
> point of spending both models only on the dangerous-but-context-dependent
> minority.

## Auto-Accept Permissions

Use with caution:

- Enable for trusted, well-defined plans
- Disable for exploratory work
- Never use dangerously-skip-permissions flag
- Configure permissions in `~/.claude/settings.json` instead

## TodoWrite Best Practices

Use TodoWrite tool to:

- Track progress on multi-step tasks
- Verify understanding of instructions
- Enable real-time steering
- Show granular implementation steps

Todo list reveals:

- Out of order steps
- Missing items
- Extra unnecessary items
- Wrong granularity
- Misinterpreted requirements
