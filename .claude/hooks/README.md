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
  carrying raw credentials to any LLM, a static stage for the container
  sandbox CLI `csb` (see **The sandbox CLI stage** below), a parallel
  Gemini+Codex AND-gate for high-risk commands, and a Gemini→Codex cascade for
  the low-risk majority.
  Logs to `~/.claude/logs/bash-review.log` and `/tmp/claude_hooks/logs/`
  (directory 0700, files 0600; the Codex entry uses `~/.codex/logs/` and
  `/tmp/codex_hooks/logs/`). Logging is best-effort and never decides a
  verdict — see **Log handling** below. The summary log holds one decision per
  physical line: a newline in the command or the reason is written as `⏎`,
  and a `|` in the reason as `/`, so a multi-line command cannot plant a
  line that reads as a second decision (the `bash-review-log` mod applies the
  same `⏎` when it matches a summary line to its detail log). When the run's
  detail log was actually written, the reason ends with
  ` detail=<detail log file name>`, and the mod pairs that line with exactly
  that file; a line without the name (older logs, or a run whose detail log was
  off or failed to write) is still paired by time and command prefix.
  `settings.json` launches it through the launcher, not bare `python3`: a hook
  command that cannot start (no interpreter, missing file) or crashes with an
  unexpected exit code is treated by Claude Code as a _non-blocking_ error —
  the command would run unreviewed. The launcher converts every such
  "review never happened" case into an explicit `ask`, and passes the normal
  vocabulary (exit 0 + decision JSON, exit 2 + stderr) through untouched.
  **The hooks run on the root-owned system python3 with `-I -S`; PATH, pyenv
  and `.python-version` have no effect.** A pyenv shim follows the cwd's
  `.python-version` and picks any name created under `~/.pyenv/versions/`, so a
  fake interpreter could take over the hook itself. The launcher therefore
  takes the first of fixed candidates (macOS:
  `/Library/Developer/CommandLineTools/usr/bin/python3`, then
  `/usr/bin/python3`; Linux: `/usr/bin/python3`) whose path, every symlink on
  the way to the file, and every directory above them are owned by root and
  not writable by group or other, that is an executable regular file, and
  whose version is 3.9 or later (asked only after the ownership check passes).
  Before starting it, the launcher unsets `DEVELOPER_DIR`, `TOOLCHAINS`,
  `SDKROOT`, `__PYVENV_LAUNCHER__`, `PYTHONEXECUTABLE`, `PYTHONHOME` and
  `PYTHONPATH` (`_sandbox_gate.LAUNCH_ENV_DROP`): a framework build reads the
  first two Python ones to rename `sys.executable` and to look for a
  `pyvenv.cfg`, even under `-I`, and the xcrun shims in `/usr/bin` follow the
  first three. With no such candidate the launcher asks
  (`信頼できるシステム python3 が無い`); it never falls back to PATH's python3.
  The rule lives twice, in the launchers' shell (`_trusted_path` /
  `_trusted_python`, which may not run an interpreter it has not trusted yet,
  and which call only `/usr/bin/stat` and `/usr/bin/readlink` by absolute
  path) and in `_sandbox_gate.trusted_path` / `trusted_python`;
  `tests/test_bash_review_launcher.py` runs one set of fixtures through both.
  `settings.json` starts every hook and the status line with `/bin/bash -p`,
  not PATH's `bash` (`/opt/homebrew/bin`, which the user can write, comes
  before `/bin`); the launchers are written for bash 3.2. `-p` (privileged
  mode, in bash 3.2 and 5 alike) makes bash skip the `$BASH_ENV` / `$ENV` file
  and the functions exported in the environment (`BASH_FUNC_*%%`). Both act
  before the launcher's first line, where its own `unset` cannot reach them, and
  an exported function shadows a builtin the launcher uses (an exported
  `printf` did, without `-p`; `tests/test_bash_review_launcher.py` pins both
  ways). `-p` on the top-level command does not reach a bash started from
  inside a hook, since `BASH_ENV` and `BASH_FUNC_*%%` stay in the environment;
  so the bash the hooks start themselves (the two hooks `format-then-lint.sh`
  runs, and the `cd` wrapper of the lint runner in `_lint_common.sh`) is
  `/bin/bash -p` too, never PATH's. No hook starts `sh`: `/bin/sh` (bash in sh mode on macOS) does not read `BASH_ENV` but does import exported functions. The csb wrapper is a `/bin/sh` script, but the `unset` and `exec` it runs are special builtins, which an exported function cannot override in POSIX mode (checked with macOS's `/bin/sh`; dash imports no functions).
  An exit 0 with empty stdout (nothing but whitespace) is one of those cases
  too: Claude Code reads it as "no decision, carry on", so the launcher turns
  it into `ask` instead of letting the command run (it used to pass through).
  The Codex variant has no such rule: for Codex, exit 0 with no output is the
  normal allow.
  See **bash-review — design rationale & threat model** below for why the
  tiers are structured this way and what they do (and do not) defend against.
- **git-push-review** (`hooks/git-push-review.sh`, matcher: `Bash`,
  no `if` filter — deliberately unconditional):
  Detects `git push` commands and forces a confirmation prompt
  (permissionDecision: ask) with a summary of the branch and an estimate of
  the commits and diffstat the push would send. The estimate follows the push
  target (the named remote and refspec, or `@{push}` /
  `branch.<b>.pushRemote` / `remote.pushDefault` for a bare `git push`) and is
  computed from local remote-tracking refs as of the last fetch; the hook
  never touches the network. A target with no tracking ref is listed as
  "commits not on any <remote> ref known locally (approximate)", never as
  "(none)", and a list longer than 10 commits ends with "(+N more)".
  Only the first five pushes of a chained command get their own target line
  and commit section; the rest are counted in one line, and any force /
  delete option among them is still called out.
  Enforces the pre-push review step of `rules/git-workflow.md`.
  The detection and the summary logic live in `_push_review_common.sh`, shared
  with the Codex copy (see **Shared libraries** below); `git-push-review.sh`
  only reads the input and shapes the output (a JSON `ask`, exit 0).
  The summary is taken from the repository the push will actually run in:
  `git -C <dir>` and a literal `cd <dir>` ahead of the push both retarget it.
  When that directory cannot be determined for certain (a variable, `cd -`,
  `~user`, `pushd` / `popd`, a subshell, a heredoc (`<<`) or comment (`#`)
  in a command that also has a `cd`, several `cd`s, a `cd` with options or
  extra operands, more than one push with a `cd` / `pushd` / `popd` (or a
  different `-C`) between them, a `..` component, a quoted `~`, a target that is not an
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
  A push written inside interpreter code is confirmed too:
  `python3 -c "subprocess.run(['git','push'])"`, `node -e`, `ruby -e`,
  `perl -e`, `deno eval`, `bun -e`, and code fed on stdin (a here-document, a
  here-string, a pipe into the interpreter). Both the argv-list spelling
  (`['git', '-C', d, 'push']`; an option value may be a name, a literal, a
  call such as `os.path.join(a, b)`, a subscript such as `sys.argv[1]`, or a
  `+` concatenation; lists joined with `+` count) and the string spelling
  (`os.system("git push")`) are read; a quoted subcommand between `git` and
  `push` (`['git','stash','push']`) is not a push. The code may `chdir()` or
  pass `-C`, so the prompt carries the "target repository could not be
  determined" note instead of a summary. The check reads the whole command,
  here-document bodies included (a here-document fed to an interpreter is a
  real way to push, so the body cannot be left out), so a here-document that
  only quotes such code — a commit message written with
  `git commit -m "$(cat <<'EOF' … EOF)"` or `git commit -F - <<'EOF'`, or
  `cat > notes.md <<'EOF'` — asks as well. That extra prompt is accepted (it
  never hides a push).
  Known gaps — pushes the detection does not see, which then run with no
  confirmation (nor does `Bash(git push:*)` match them): a push written to a
  file and run from there (`echo 'git push' > p.sh && sh p.sh`), a push
  assembled from pieces inside interpreter code (`'pu' + 'sh'`,
  `[*GIT, 'push']`, `cmd.append('push')`), an option value with parentheses
  nested three deep, a line that starts with `#` inside a multi-line string
  (the interpreter check drops it as a comment, and a push carried on it with
  it: `s = '''` then `# '''; subprocess.run(['git','push'])`), awk's
  `system()`, TypeScript runners (`tsx`, `ts-node -e`; only `node -e` /
  `--eval` / `-p`, `deno eval` and `bun -e` are known), and an interpreter
  option glued to its code (`python3 -cprint(1)`: `-c` with no space before
  the code).
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
  backstop for when the hook layer is off. The hook itself no longer fails
  open: its entry carries `"timeout": 60` and `"onFailure": "block"`, so a
  hook that hangs, cannot start or crashes blocks the command (inside the
  script a broken summary still degrades quietly, `|| exit 0`). That rule
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
   The hook runs in every repository you work in, so it does not impose a
   default style on code it does not own. **prettier** runs only when a config
   is found from the edited file up to the git root (`.prettierrc*`,
   `prettier.config.*`, or a `"prettier"` key in `package.json`; a `prettier`
   devDependency alone is not a config). **clang-format** runs only when
   `.clang-format` or `_clang-format` is found the same way. **shfmt** takes its
   indent from `.editorconfig` when that file sets `indent_style` /
   `indent_size` / `tab_width` for shell scripts (`[*]`, `[**]` or a `sh` /
   `bash` section); otherwise it runs with `-i 2`. A symlinked target is never
   formatted: the formatters refuse to replace a symlink, and formatting the
   link's target could rewrite a file in another repository. Why a file was not
   formatted is written to `format.log` on the `DONE` line (for example
   `skipped: no prettier config (.prettierrc*, prettier.config.*, package.json "prettier") up to <root>`).
2. **lint** (`hooks/lint.sh`):
   Static analysis after formatting. Exits with code 2 to feed errors
   back to Claude and trigger self-correction. Logs to
   `~/.claude/logs/lint.log`.
   A clean exit does not mean something was checked. `hook_lint_file` leaves the
   number of linters that actually ran in the global `HOOK_LINT_RAN`, and the
   last command's exit status in `HOOK_LINT_LAST_RC`. `lint.sh` sends the "Lint
   Passed" notification only when `HOOK_LINT_RAN` is above 0. When no linter ran for the file, the hook returns 0 without a notification, for example when the extension has no linter, the linter is not installed, it could not start (exit 126/127 or a
   launcher's boilerplate such as pyenv's `command not found`, logged as `NOTICE`), it timed out, `docker compose config` failed for an environment reason or on a fragment, mypy failed without naming the file, or cargo exited non-zero with no `path:line:col:` diagnostic at all (clippy did not complete; a notice). A launch failure or a timeout is a notice in the
   log, never an exit-2 finding: the model could not fix it by editing.

   Only five linters run under a time limit: hadolint, `docker compose config`,
   tsc, mypy and clippy. The others (ESLint, ruff, bandit, `go vet`,
   staticcheck, checkstyle, cppcheck, rubocop, phpstan, `php -l`, shellcheck)
   run with none. The default is per tool and `HOOK_LINT_TIMEOUT_<NAME>`
   (seconds) overrides it: `HADOLINT` 20, `COMPOSE` 15, `TSC` 45, `MYPY` 30,
   `CLIPPY` 45. A value that is not a positive number (`0` and `0.0` included)
   falls back to the tool's default; there is no "0 means no limit". The limit
   sends TERM first, and KILL 2 s later to a process that ignores TERM. A
   timeout is logged as `NOTICE: <tool> timed out after Ns; skipped` and is not
   a finding. What is handed back to the model is capped: at most
   `HOOK_LINT_MAX_LINES` lines (default 200) and `HOOK_LINT_MAX_BYTES` bytes
   (default 16384) per file, followed by the `… truncated …` line that
   `hook_cap_output` writes (the log keeps the same cut text). The Codex
   `lint.sh`, which can lint up to 50 files in one `apply_patch`, also caps the
   total across files with `HOOK_LINT_TOTAL_MAX_LINES` (default 400) and
   `HOOK_LINT_TOTAL_MAX_BYTES` (default 32768). For each of these four, a value
   that is not a positive integer falls back to the default.

   Dockerfiles and compose files are chosen by file name, not extension.
   `Dockerfile`, `Dockerfile.*`, `*.Dockerfile` and `Containerfile` go to
   **hadolint** (`*.dockerignore` is not a Dockerfile), with the nearest
   `.hadolint.yaml` / `.hadolint.yml` from the file up to the git root passed as
   `--config`, because hadolint itself reads only the config in its cwd.
   `compose.y(a)ml`, `compose.*.y(a)ml` and the `docker-compose` forms go to
   `docker compose -f <file> config -q --no-interpolate`, run in the file's
   directory (`--no-interpolate` so an unset variable is not a finding). A
   failure that is the environment's (a missing `env_file`, no daemon, no
   compose plugin, a credential helper, a missing context) is a notice, not a
   finding. A file that is not the base (`compose.override.yml`,
   `compose.prod.yml`) is a fragment that is merged with a base, so "neither an
   image nor a build context" and an undefined service, volume, network, secret
   or config are not judged on it alone. If `hadolint` or `docker` is not
   installed, the file is not linted, and no "Lint Passed" is sent for it.

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
  diff against `HEAD`. The selection itself comes from
  `hook_git_changed_files` in `_hook_common.sh`; if that library cannot be
  loaded the audit stands down (exit 0, message on stderr), like the
  missing-`jq` case.
  It audits only the lines the working tree **adds** relative to `HEAD`
  (`hook_git_added_lines`, `_hook_common.sh`: `git diff -U0 HEAD`, so staged and
  unstaged edits count as one). A file with no `HEAD` version (untracked, newly
  staged, or every file before the first commit) has every line added, so it is
  read whole. The destination of a `git mv` is audited only for what differs
  from its source (an unstaged `mv`, and a rewrite below 50% similarity, scan
  the whole file). Reading the whole file would block every turn on a legitimate
  `console.log` that is already committed (a CLI's output, a logger) for as long
  as that file is dirty. The files covered are `.js` `.jsx` `.ts` `.tsx` `.mjs`
  `.cjs` `.mts` `.cts` `.vue` (`console.log(`, `debugger`) and `.py`
  (`breakpoint()`, `pdb.set_trace()`). The audit also stands down, like the
  `hook_git_changed_files` case, when `_hook_common.sh` loads but lacks
  `hook_git_added_lines`.
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

These six files hold the logic shared between the two sides — the
lint/format matrices, bash-review's verdict logic, the sandbox CLI's contract
and git-push-review's detection and summary. They live here as the _only_ copies: `.codex/hooks/` carries neither a duplicate nor a link, and its
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
hooks on events. `git-push-review.sh` is the one that is kept deliberately thin:
each side's copy is only an adapter (read the input, drop commands that cannot
be a push, load `_push_review_common.sh`, shape the result), and everything else
is in the library. See `.codex/hooks/README.md` for the full table and those
differences.

- **`_hook_common.sh`**: `hook_log` (timestamped, size-capped),
  `hook_notify` (terminal-notifier / osascript / notify-send), and
  `hook_find_nearest_bin` (nearest `node_modules/.bin` lookup used by lint and
  format), and `hook_git_changed_files <repo_root>` (the git-derived list of
  changed files: the diff against `HEAD` -- or against the index before the
  first commit -- then untracked files; repo-relative, NUL-separated; shared by
  both `stop-audit.sh` copies and the Codex `auto-format.sh` / `lint.sh` git
  fallback). Also:
  - `hook_find_nearest_file <start_dir> <root_dir> <name>...`: the nearest of
    the named files, walking up to the root, as `hook_find_nearest_bin` does for
    `node_modules/.bin`. Used for `.clang-format`, `.hadolint.yaml` and
    `tsconfig.json`.
  - `hook_git_added_lines <repo_root> <path>`: the added lines of one file as
    `lineno:text` (see stop-audit).
  - `hook_cap_output [max_lines] [max_bytes]`: keeps the first 200 lines / 16384
    bytes of stdin by default (`HOOK_LINT_MAX_LINES` and `HOOK_LINT_MAX_BYTES`
    override them; a limit that is not a positive integer, given or from the
    environment, falls back to the default) and drops an incomplete UTF-8 sequence at the cut. Input within the limits (or any input when no temporary file can be made) passes through unchanged. When it cuts, it appends exactly one of these two lines, with N the dropped line count, M
    the line limit and B the byte limit:
    `… truncated N lines (output capped at M lines / B bytes)`, or, when one
    long line alone was cut,
    `… truncated (a long line was cut; output capped at M lines / B bytes)`.
  - `hook_run_timeout <seconds> <command> ...`: runs a command with a time limit
    and exits 124 on timeout. It uses `timeout`, then `gtimeout`, then a
    pure-bash fallback, because macOS has no `timeout`. On timeout it sends
    TERM, and KILL 2 s later to what ignores TERM (the whole process group, so
    a grandchild holding the pipe does not keep the caller waiting); a KILL
    also exits 124. A limit that is not a positive number runs the command
    with no limit: this helper has no default of its own, so a caller with one
    (`_lint_timeout`) checks the value first and substitutes its default.
- **`_lint_common.sh`**: `hook_lint_file` — the per-language lint matrix.
- **`_format_common.sh`**: `hook_format_file` — the per-language formatter matrix.
- **`_push_review_common.sh`**: git-push-review's push detection and its
  confirmation text (push target, `WARNING` lines, branch / commit summary).
  One entry point, `hook_push_review_run <raw stdin> <command>`, which returns
  `0` (a body is in the global `push_review_body`), `1` (not a push) or
  anything else (unexpected). It must stay the last function in the file.
- **`_bash_review_common.py`**: bash-review's verdict logic and review calls.
- **`_sandbox_gate.py`**: the sandbox CLI's contract (names, the work container's
  profile and its inspect check, the `csb` argv parser and the hook's classifier,
  the patch and create plans, the dialog summaries, the `~/.local/bin/csb`
  wrapper text), read by both `bash-review.py` entries and by `claude_sandbox.py`.
  The sandbox code the two entries keep in their own copies sits between
  `# >>> shared:` / `# <<< shared:` markers, and
  `tests/test_bash_review_sandbox.py` pins those regions byte for byte.

Contract for the shell ones: they define functions and nothing else. Sourcing
must not print, `mkdir`, or touch `set`/`IFS`/`trap`/cwd — the Codex `lint.sh`
sources them before `exec 1>/dev/null`, so any output at source time would land
in the structured-output channel Codex parses and fail the hook. Function names
are namespaced `hook_` because bash is dynamically scoped and a collision with a
caller's local fails silently. Variables inside them are not prefixed
(`FILE_PATH`, `EXTENSION`, …), so callers must not rely on names they share.

`_push_review_common.sh` is a **private library**, an exception to the `hook_`
naming rule: its one public name is the entry point `hook_push_review_run`, and
the helpers behind it (`strip_quoted_ranges`, `git_c_dir_from_words`,
`build_push_report`, …) keep the plain names they had when they sat in the hook.
They were moved verbatim, and renaming them would defeat reviewing that move as a
plain `diff`. The `gp_*` / `push_report` / `hd_*` variables are intentional
globals that carry results between those functions (a `$(…)` between them would
strip a trailing newline).

`git-push-review.sh` does **not** fail open when it cannot load this library, in
contrast to `lint.sh` / `auto-format.sh`: it is the gate in front of `git push`,
and "could not load" is not evidence that the command is not a push. The load is
guarded by a cheap "no `push` / `send-pack` in the command" check that runs
first, so ordinary Bash calls never parse the library; a command that passes
the check and then meets a missing, empty, truncated or syntactically broken
library, a missing required function, or an entry point that returns anything
but `0` / `1`, degrades to a fixed-text confirmation (Claude: a JSON `ask`,
exit 0; Codex: stderr, exit 2) saying the library could not be loaded and the
push must be reviewed manually, plus a `could not load _push_review_common.sh
from <dir>` line on stderr. That is why the entry point must be the last
function: the adapters take "it is defined" to mean "the whole file was read".

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
   known remaining false positive, and so is a written file (`cat > f <<'EOF'`)
   whose body has a line that is just `env` / `set` / `export`, or starts with
   `psql` / `mysql`.

   **Mirror of the `permissions.deny` Bash rules.** Those rules match the
   command's prefix, so `/usr/bin/printenv KEY`, `(printenv KEY)`,
   `/opt/homebrew/bin/gh auth token`, `env gh auth token` and
   `git -C . credential fill` slip past them. Denial resolves the same names
   the deny rules list: the executables `printenv`, `shutdown`, `reboot`,
   `psql`, `mysql`, `mongod` (next to `sudo`, `curl`, … above); the
   subcommands `git credential …` / `git credential-<helper>` (after git's
   global options), `gh auth token`, `gh auth status --show-token|-t` and
   `security find-generic-password|find-internet-password|dump-keychain`
   (after `security`'s own options); and the environment dumps, i.e. `env` with
   no command (flags and assignments only), bare `set`, `export` / `export -p`,
   and `declare` / `typeset` with no names. Those are matched case-sensitively,
   so a Dockerfile line `ENV FOO=bar` in a heredoc is not `env`. `set -e`,
   `export FOO=1`, `declare -A m` and `/usr/bin/env python3 x` still run. A
   named `declare -p KEY` is not denied but goes to the high-risk gate (step 3).
   The DB clients are denied only on the host: `docker compose exec db psql`
   is a high-risk label, not a deny.

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

   Next, a command that **writes to the harness's own configuration** is an
   `ask` (Codex variant: block) before any model is consulted
   (`harness_write_ask_reason`), so a single Gemini `ALLOW` cannot let a command
   rewrite the gate that reviews it. The protected set mirrors the `Edit(…)`
   rules in `permissions.ask` — `.claude/settings.json`,
   `.claude/settings.local.json`, `.claude/hooks/`, `.claude/mods/`,
   `.codex/hooks/`, `~/.codex/`, and the instruction files read into every
   session (`.claude/CLAUDE.md`, `.claude/rules/`, `.codex/AGENTS.md`,
   `.gemini/GEMINI.md`) — plus the bare `.claude` / `.codex` / `.gemini`
   directory as a destination, and the sandbox CLI's state and launcher
   (`~/.local/state/claude-sandbox/`, `~/.local/bin/csb`), whose records a
   rewrite could forge. Skills, agents and commands are deliberately left
   out: they load only when used and are edited far more often, so a prompt on
   every edit would train approving without reading;
   `tests/test_config_wiring.py` fails if an `Edit` ask path is not covered. It
   recognises write _forms_, not mentions, because reads of these paths
   (`sed -n`, `git diff`, `cat | jq`) were about a quarter of all logged commands:
   a write redirect (`>`, `>>`, `>|`, `&>`, `>&`) whose target is protected; a
   known writer with a protected destination (`tee`, `rm`, `truncate`, `touch`,
   `chmod`, `mv`; `cp` / `install` / `ln` / `rsync` destinations including
   `-t DIR`; `dd of=`; `sed -i`; `perl -i`); a sed `w` / `W` command or `s///w`
   flag whose file is protected, with or without `-i` (the script is taken
   from `-e` / `--expression` or the first operand, and the regex and
   replacement of `s#.claude/hooks#x#` are skipped, so naming the path there is
   not a write); a docker bind mount whose host source is protected, inside a
   protected directory or above one (`-v ~/.claude:/c`, `-v ~:/h`, `-v /:/host`,
   `~/.local`, plus the shell rc files `.zshrc` / `.bashrc` / `.profile` / …, which
   every Bash tool shell reads) for `docker run` / `create` /
   `compose run`, unless it is read-only (`:ro`, `readonly`; the high-risk
   gate still labels it), and a `docker cp` whose host destination is
   protected; the same forms inside shell code
   (`sh -c`, `eval`, `bash <<EOF`, `source <<EOF`); and a protected path in an
   awk program. A target that expands (glob, brace, `$VAR`) counts when the
   expansion could land on a protected path (`.claude/sett*`, `$D/settings.json`).
   A target is matched as written **and** in a lexically collapsed spelling (`//`
   squeezed, `/./` dropped, `x/..` resolved), so `.claude//settings.json`,
   `.claude/./settings.json` and `.claude/x/../settings.json` ask like the plain
   one. The written form is always checked too: collapsing can change the
   meaning once a symlink, variable or glob is involved. The two places that
   match free text rather than a token (a redirect line the shell parser gave up
   on, an awk program) collapse each word that contains a `/` the same way.
   Heredoc bodies, multi-line quoted strings and comments are not read, as for
   denial. Code in another language (`python -c`, `node -e`, `python3 - <<EOF`)
   is deliberately left to the high-risk gate: whether it reads or writes cannot
   be decided statically, and in the replayed log it was almost always analysis
   of the hook's own source.

   With the session cwd (`harness_write_ask_reason(…, cwd=…)`; `None` keeps the
   lexical check above and touches no filesystem; `HARNESS_CWD_UNKNOWN` resolves
   absolute destinations and sends every relative write to the human), a
   command the lexical check passes is checked again: relative destinations are joined to the cwd;
   literal `cd` / `pushd` / `popd` chains are followed (`&&` narrows, `;` / `||` /
   pipes keep every possible cwd, `( … )`, `$( … )` and `sh -c` do not leak); a
   directory destination also counts `dir/basename(src)`; links made earlier in
   the same command and literal variables (`S=…; cd "$S"`) are followed; and
   each candidate is matched as spelled and via `os.path.realpath` (an error
   asks). A relative write after a `cd` that cannot be resolved (an expansion,
   `cd -`, CDPATH, a reassigned HOME / PWD, a function or alias definition)
   asks. `~/.local/bin` is protected as a directory, and a command word that
   hides its name (`$'…'`, `cs${x}b`, `c\sb`, `${x:-cp}`, zsh's `=cp`, an
   unquoted glob such as `c?` or `/bin/c[p]`) asks for any command. A command
   word taken from a variable (`$CP`) is resolved from a literal assignment in
   the same command; when it cannot be, it asks only next to a protected path.
   Values built from `$(mktemp)` (under a temp directory), `$(whoami)` /
   `$USER`, a literal `for` list or `$PWD` are resolved for destinations and
   links (never for command words or a cd), unless the same command can change
   what they rely on: TMPDIR / PATH / USER / LOGNAME / HOME (any assignment,
   `+=`, read, printf -v, typeset/declare/local/export, unset, `${X:=…}`, a
   prefix or `env`), zsh's path/fpath/cdpath and alias/function/command tables,
   hash / alias / enable / a function, `env -i` / `exec -c`, `$'…'`, eval /
   source, or a user switch.

   zsh named directories (`~D`, `hash -d`) are expanded before matching; an
   undecidable `~name` asks. Braces in destinations (`{a,b}`, `{1..3}`, nested)
   are expanded before matching, at most 64 alternatives per word and 4096 per
   command; past either limit the destination asks. mktemp counts as a writer. Protected also: any
   `*.zwc`, `.zcompdump*`, `~/.zsh_secrets`, `~/.docker/completions`, the
   Homebrew prefix (`/opt/homebrew`, `/home/linuxbrew/.linuxbrew`,
   `$HOMEBREW_PREFIX` …). A glob command word that starts with a letter and
   matches a deny name (`/bin/r? -rf /`, `/usr/bin/cur? x`) is denied only where
   the shell runs it as a command: in the command text (also inside `$(…)` and
   in backticks within double quotes), in a multi-line `sh -c` script, in an
   unquoted heredoc's `$(…)`, on the line after an arithmetic `<<` (`$(( ))`,
   `(( ))`, `$[ ]`; after `let x=1<<2` the shell reads a heredoc, so the next
   line is data), and in a body that a shell reads as its script. That covers a
   shell (`sh`, `bash`, `zsh`, `dash`, `ksh`, …, also behind `env` / `timeout` /
   `command` / an absolute path, or at the end of a pipe) with no script
   operand, with `-s`, `-`, `/dev/stdin`, `/dev/fd/0` or `/proc/self/fd/0`,
   after value-taking options (`-o OPT`, `+o OPT`, `-O OPT`, `--rcfile FILE`,
   `--init-file FILE`), or with `-c` given the code from stdin (`"$(cat)"`,
   `"$(cat -)"`, `"$(cat /dev/stdin)"`, `"$(< /dev/stdin)"`); `source` / `.` of
   `/dev/stdin` or of a process substitution; and `bash <(…)`. As with the
   literal spelling, a single-line `sh -c '…'` / `eval '…'` is not denied; the
   glob there asks as a command word that hides its name. Elsewhere (a heredoc
   body for another program or for a script file, quoted text) the glob spelling
   is not denied. Shapes the earlier rule denied — a heredoc body for a command
   that is not data-only, such as `python3 - <<EOF`, `bash script.sh <<EOF`,
   `cat > notes.md <<EOF` — ask, naming the deny target; the rest (`echo '…' >
   notes.md`, commit messages, PR bodies) go through the usual review. A glob
   that does not start with a letter (`?url`, `[c]url`) asks, naming the deny
   target it can expand to. The
   protected paths also include `~/.pyenv` (and `$PYENV_ROOT`), the shell
   start-up files wherever they are (`.zshrc`, `.zshenv`, `.zprofile`,
   `.zlogin`, `.zlogout`, `.bashrc`, `.bash_profile`, `.bash_login`,
   `.profile`) and `~/.oh-my-zsh`.

   Both entry points pass one cwd value to this check and to the sandbox stage:
   the payload's `cwd` when it is an absolute path string without NUL, else the
   hook's own cwd (`session_cwd`); when neither is usable they pass
   `HARNESS_CWD_UNKNOWN` (never `None`, which would let a write through a
   variable, a `cd` or a named directory pass unasked), and the sandbox CLI is
   then unverifiable. The Codex entry uses the shell tool's `tool_input.workdir`
   when it is there: an absolute path as is, a relative one joined to the
   session cwd, and anything else (`~`, empty, NUL, not a string) as
   `HARNESS_CWD_UNKNOWN`.

   Closed now that the cwd is passed (the lexical check alone still misses them,
   pinned as `HARNESS_WRITE_RESIDUAL_CASES` with `cwd=None`, and the entry-point
   tests pin them asked):
   - a `cd` / `pushd` into a protected directory followed by a relative write
     (`cd .claude/hooks && echo x > a.py`, `cd ~/.local/bin && cp x .`);
   - a protected path held in a literal variable (`x=.claude/settings.json; tee $x`);
   - a relative write nested three `sh -c` levels deep.

   Known gaps that remain, each pinned by a test so closing one is a deliberate
   change:
   - a bind mount of the working tree (`-v "$PWD":/app`, `-v .:/app`) when that
     tree holds a `.claude` directory — this repository is exactly that case.
     Flagging every cwd mount would put the everyday
     `docker run -v "$PWD":/app … npm test` behind a prompt;
   - a hard link to a protected file, made in another command: `realpath` does
     not see a hard link;
   - writers not on the list (`openssl … -out`, `tar -C`, `unzip -d`,
     `git checkout -- <path>` / `git restore`, `yq -i`, BSD `sed -I`, …), and a
     git branch switch that replaces hook files;
   - tools that write into a protected tree as their job: `pyenv install` and
     `pip install` under `~/.pyenv`, `brew install` under the Homebrew prefix;
   - a destination built with `$( … )` (`> "$(dirname x)/settings.json"`);
   - a glob command word that does not start with a letter (`*url`, `[c]url`)
     asks but is never denied (above): a DENY cannot be approved, and denying
     such globs gave false positives;
   - a glob in a subcommand position (`git cred* fill`) does not hit a deny;
   - the single-line `sh -c '…'` / `eval '…'` named above: even a literal
     `rm -rf /` there asks rather than being denied;
   - Not denied either way: a here-string or quoted text piped into a shell
     (`bash <<< '…'`, `printf '…' | sh`), which reaches the two-model review as
     stdin into a shell.
   - inside a `case` branch (`case x in x) … ;; esac`), neither the literal nor
     the glob spelling is denied: it goes to the high-risk gate's review of the
     wrapped command;
   - A glob spelling in a heredoc body only asks (the literal spelling is denied)
     for a few other ways a shell reads its stdin as code:
     `bash -c "$(cat /dev/fd/0)"`, `sh -c "$(head -c N)"`, a one-line `-c` that
     itself reads stdin (`bash -c 'eval "$(cat)"'`, `bash -c 'source /dev/stdin'`,
     `bash -c '. /dev/stdin'`), and `zsh --emulate sh`.
   - a `cd` the hook cannot see: a CDPATH set in the Bash tool's shell but not in
     the hook's environment, and a `cd` behind a variable command word
     (`c=cd; $c dir`);
   - a path assembled at run time (`> $D/hooks/x`; a protected file name such as
     `$D/settings.json` is still caught);
   - shell code that reaches a shell on stdin as one word (`echo '…' | sh`,
     `bash <<< '…'`), and shell code nested four or more `sh -c` levels deep
     (both still reach the high-risk gate, as a stdin interpreter or `sh -c`);
   - a sed script read from a file (`sed -f prog.sed`) or spread over several
     lines inside one quoted word.

   Three false-positive sources are accepted: a command the parser gives up on
   (`((`, `$[`) has its heredoc bodies read as commands; a heredoc whose body
   _text_ pipes into a shell (`… | sh`) is treated as shell code fed on stdin; and
   a command that passes multi-line code to `sh -c` also has its other multi-line
   quoted text read as commands.
   Replaying 981 logged commands changed one decision (an AND-gate command became
   an ask); the other changes came from probes written to test this check.

   Safe-skip is the conservative side: it matches the raw command string against
   an allowlist, so anything wrapped in grammar simply fails to match and goes
   to review. The resolver is consulted only by the exclusions below (grep / rg
   / tmux / output-file flags), and only to send more commands to review. A sensitive path (`.env`, `id_rsa`,
   `.ssh`, …, and the CLI credential stores `.docker` (its `config.json`),
   `.kube`, `.gnupg`, `.config/gcloud`, `.config/op`, `.azure`) forces review
   even when the prefix looks safe — also written relative to the cwd
   (`cat .docker/config.json` when the cwd is `~`). So do the reads
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
   `nghp_…` as one word.
   The rules live in two tables. `_SECRET_SCANNERS` holds the formats that
   are credentials wherever they appear, and the session-dossier share
   redaction reuses it (it replaces each whole match). The prefix formats
   include `sk_test_` / `rk_test_` Stripe keys and `whsec_` webhook secrets,
   Shopify `shp…_`, Linear `lin_api_`, Netlify `nf[pco]_`, Databricks `dapi…`,
   Twilio `SK…`, Discord and Telegram bot tokens, and URL credentials with an
   empty user (`redis://:pw@host`). `_CONTEXT_SCANNERS` holds the rules
   where only the surrounding text says "this is a credential". `scan_secrets`
   (bash-review, `secret_scan.py`, gemini-consultant) reads both tables; the
   dossier does not read this one, because whole-match redaction would wipe
   the key it keeps (`DB_PASS=[REDACTED]`). Those rules
   cover upper-case `*_PASS` / `*_PWD` / `*_PAT` variables and bare `PASS=`
   (`MYSQL_PWD`, `AZURE_DEVOPS_EXT_PAT`); quoted keys in JSON / PHP / Ruby
   literals (`{"pwd": …}`, `'password' => …`); `curl -u` / `--user` /
   `--proxy-user` `user:pw`; `sshpass -p`; a netrc
   `machine … login … password …` line; `Authorization: Token|ApiKey …`; and
   Azure's `AccountKey=…`. A context rule matches nothing outside its context:
   `docker run -u 1000:1000` is not `curl -u`.
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
   options (`reset --har`, `clean --forc`, `push --forc`), the container,
   git and environment forms listed below, …; see
   `high_risk_label`) are reviewed by Gemini
   **and** Codex run _in parallel_, and their verdicts are combined as an
   AND-gate (`combine_high_risk_verdicts`): `allow` only when **both** return
   `ALLOW`, `deny` only when **both** return `DENY`, and everything else —
   disagreement, `ASK`, or `ERROR` — resolves to `ask` with both verdicts
   attached. A single model's approval never auto-runs a high-risk command;
   convincing just one is not enough. Reserving both models for this
   dangerous-but-ambiguous minority is what keeps the AND-gate's latency cost off
   the common path.

   **Containers.** The daemon is root-equivalent, so these docker /
   docker compose shapes are high-risk:
   - `docker compose up|run|create` (and `docker-compose`), always: compose
     files are not parsed, so whatever they mount or grant is unseen.
     Compose's own global flags (`-f`, `-p`, `--env-file`, `--profile`, …) are
     skipped first; `compose ps|logs|config|build|down` stay low-risk.
   - `run` / `create` with `--privileged`, `--pid|--userns|--ipc|--uts|--cgroupns=host`,
     `--network|--net=host|container:*`, a dangerous `--cap-add` (`ALL`,
     `SYS_ADMIN`, `SYS_PTRACE`, `SYS_MODULE`, `NET_ADMIN`, `DAC_READ_SEARCH`,
     `DAC_OVERRIDE`, `SYS_RAWIO`, `BPF`, `PERFMON`, …, any case, `CAP_` prefix),
     `--security-opt seccomp|apparmor|systempaths=unconfined` / `label=disable`,
     `--device`, `--device-cgroup-rule`, `--volumes-from` or `--env-file`.
   - A bind mount whose source is the host root (`/`, `/./`, `/etc/..`, `//`),
     the home directory or above (`~`, `$HOME`, `${HOME}`, the literal
     `$HOME` path, `/Users`, `/home`), a system path (`/etc`, `/root`, `/dev`,
     `/proc`, …; a read-only `/etc/localtime` is fine), `docker.sock` or a
     directory holding it, a credential store (`~/.ssh`, `~/.aws`,
     `~/.docker`, `~/.kube`, `~/.gnupg`, `~/.config`, `~/.claude`, `~/.netrc`,
     …, also as a relative `./.ssh`), a relative path that climbs out (`../x`),
     or a source the hook cannot resolve (`$SRC`, `$(…)`). A read-only mount is
     still labelled. Sources are normalised first: `$PWD` / `${PWD}` /
     `$(pwd)` / `` `pwd` `` become `.`, `~` / `$HOME` / `${HOME}` become the
     value of `HOME`, and only then `normpath` runs (so `~/..` is `/Users`, not
     `..`). The flags are read with docker's own rules: glued `-v/:/h`,
     bundled `-itv /:/h` / `-itv/:/h`, `-v=…`, `--volume=`,
     `--mount type=bind,src=|source=`. Scanning stops at the image, so
     `docker run img ls -v /` is the container's argument, not a mount. Named
     volumes and `-v /data` (anonymous) are not host paths. The working-tree
     mount `docker run --rm -v "$PWD":/app node:20 npm test` stays low-risk.
   - A remote or alternate daemon: `-H` / `--host`, `--context` / `-c`,
     `--config`, a `DOCKER_HOST=` / `DOCKER_CONTEXT=` / `DOCKER_CONFIG=` prefix
     or `export`, and `docker context use|create|update`.
   - Destructive or host-affecting: `system prune`, `volume rm|prune`,
     `image prune -a`, `compose down -v`, `compose rm -v`, `rm -f|-v`, `cp`
     (either direction), `push`, `login`, `commit`, `network connect`, and
     `exec` (or `compose exec`) with `-u 0|root|0:*`, `--privileged`,
     `--env-file`, or a `psql` / `mysql` / `mongod` command inside.

   Management forms (`docker container run|exec|cp|commit|rm`,
   `docker image push`) are classified like the short ones. Labels use fixed
   words only and never echo a mount source, a `-H` value or a file path.
   Nothing here touches `docker exec <container> sh` or the sandbox CLI
   (`csb …`); a pinned corpus in `tests/test_bash_review_gate_hardening.py`
   keeps it that way.

   **git and gh.** A git setting that runs a program makes a later safe-skipped
   `git status` / `git diff` run it, so writing one is high-risk. That covers
   `git config` in any scope, `-f FILE` and the `git config set` form, plus
   `git -c KEY=VALUE`, `--config-env` and the environment (`GIT_CONFIG_*`,
   `GIT_SSH_COMMAND`, `GIT_EXTERNAL_DIFF`, `GIT_ASKPASS`, `GIT_PAGER`, `PAGER`,
   `EDITOR`, `VISUAL`, `LESS*`, …). The
   keys are `core.fsmonitor`, `core.hooksPath`, `core.sshCommand`,
   `core.pager`, `core.editor`, `core.askPass`, `sequence.editor`, `pager.*`,
   `diff.external`, `diff.*.textconv|command`, `merge.*.driver`,
   `filter.*.clean|smudge|process`, `credential[.*].helper`, `include.path`,
   `includeIf.*`, `gpg[.*].program`, `remote.*.uploadpack|receivepack`,
   `protocol.*allow`, and an `alias.*` whose value starts with `!` or carries
   `-c`. Reads (`--get*`, `--list`, one argument) and `--unset` are not writes;
   a pager or editor that is a bare `cat`, `less`, `more`, `most`, `delta`,
   `true`, `false`, `:`, `vi`, `vim`, `nvim`, `nano`, `emacs` or `code`, `less`
   with display flags only (`-R`, `-FRX`; not `+cmd`, `-o`/`-O`, `-k` or a long
   option) or `code --wait` / `-w` is left alone, as is a boolean
   `core.fsmonitor` (`git -c core.pager=cat log`, `GIT_PAGER=cat git log`,
   `GIT_EDITOR=: git rebase --continue`); any other argument is high-risk.
   `PAGER`, `EDITOR` and `VISUAL` (git's fallbacks, also read by man and
   others) are judged the same way on any command; `LESS` is left alone only
   when it holds display flags (`-R`, `FRX`); `LESSOPEN`, `LESSCLOSE`,
   `LESSECHO`, `LESSEDIT`, `LESSGLOBALTAGS` and the `LESSKEY*` variables are
   high-risk whenever they hold a value. These environment names are judged
   wherever a value is given to them: a prefix (`PAGER=… git log`), `export`,
   `declare` / `typeset`, `readonly`, `local` (zsh's works outside a function
   too), and a bare assignment, which a later statement of the same command
   sees (`PAGER='less +!id'; git -p log`, the assignment on the line before,
   one beside `then` / `do` / `{` or a redirection, or one behind a subshell's
   `(` stuck to it: `(PAGER=…; git -p log)`). When the value cannot be read
   statically — `read [-opts] NAME…`, `mapfile` / `readarray` / `getopts`,
   `printf -v NAME` (zsh: `print -v NAME`, also inside a bundle such as
   `print -rv NAME`), `${NAME:=…}` /
   `${NAME=…}` (zsh: `${NAME::=…}`), and the variable of `for NAME in …` — a
   name on these lists is high-risk whatever the value (an option value of
   `read` counts as a name too, on the safe side, and an array element such as
   `PAGER[0]` counts as its array, whose element 0 is `$PAGER`); other names
   are left alone (`read line`, `printf -v out %s x`, `: ${FOO:=1}`). The
   `${NAME:=…}` forms are matched on the raw command text, so they count
   inside quotes, a heredoc body or a commit message too, and the judgement
   then falls to the AND gate. An unquoted `PAGER[0]=…` and `select NAME in …`
   were high-risk already (`wrapped command`). Not followed (they reach the
   single model): a value set by a sourced file (`source` / `.`; `eval` is
   high-risk on its own), a nameref (`declare -n p=PAGER; p=…`), an arithmetic
   assignment (`(( PAGER = … ))`), a name built at run time (`export "$name=…"`),
   and zsh's tied variables (`export -T PAGER pager; pager=(…)` sets `PAGER`
   through the lower-case array). Also high-risk: the execution hooks
   `rebase -x|--exec`, `bisect run`, `submodule foreach`, `filter-branch`,
   `difftool -x|--extcmd`, `grep -O`, `--upload-pack` / `--receive-pack` /
   `clone -u` / `push --exec`, `--exec-path=`; the working-tree discards
   `checkout -- .` / `checkout .` / `checkout -f` and `restore .` (unless only
   `--staged`) for a whole-tree pathspec (`.`, `:/`, `*`, `..`); `branch -D`
   (`--delete --force`, `-df`); `stash drop|clear`; `gh repo delete`,
   `gh release delete[-asset]`. `git checkout feature-branch`,
   `git checkout -- file`, `git restore --staged file` and
   `git config user.name x` stay low-risk. A named `declare -p KEY` /
   `typeset -p KEY` (prints a variable) is high-risk too.

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
   `bash-review-launcher.sh` turns a missing trusted system python3, a missing
   `bash-review.py`, a crash (any exit other than 0/2), or an exit 0 with
   empty stdout (no decision emitted) into an explicit
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
   `$(`. `git-push-review.sh` fails closed the same way: its entry carries
   `"timeout": 60` and `"onFailure": "block"`, so a hook that cannot start,
   hangs past its timeout or crashes (exits with anything other than 0 or 2)
   blocks the Bash command it was run for. The entry has no `if` filter (see the
   PreToolUse section) and the script decides inside whether a command is a
   push, so the hook runs on **every** Bash call: a broken `git-push-review.sh`
   stops all Bash commands, not only pushes. Failures the script handles itself
   do not block: a summary it cannot build degrades quietly (`|| exit 0`), and a
   library it cannot load is an explicit `ask` (see **Shared libraries**). The
   trade is intended: a gate in front of `git push` that fails open is no gate,
   and the Codex side, which has no `onFailure` (see Known gaps), cannot do this
   at all. `Bash(git push:*)` in `ask`
   remains the backstop for the plain spelling with the hook layer disabled.
   Should the hook layer be disabled altogether, `permissions.deny` still
   mirrors the hook's context-free hard-deny set — `sudo`/`su`/`doas`/`pkexec`,
   `ssh`/`slogin`/`scp`/`sftp`/`telnet`/`socat`/`ftp`/`curl`/`wget`/`nc`/`netcat`/`ncat`,
   `dd`/`shred`/`mkfs`, `printenv`/`shutdown`/`reboot`/`psql`/`mysql`/`mongod` —
   so none of them run
   unreviewed even with the hook down. The one residual: `permissions.deny`
   matches on word boundaries and cannot express the `mkfs.<fs>` family
   (`mkfs.ext4`, `mkfs.xfs`, …) in a single rule, so the common members are
   enumerated and exotic filesystems stay covered by the hook alone
   (`test_config_wiring.py` guards the mirror).

**The sandbox CLI stage (`csb`).** Right after the harness-write check and
before safe-skip, both entry points pass the command to
`classify_sandbox_command` (`_sandbox_gate.py`; imported by the entry points,
not by `_bash_review_common.py`, which `_sandbox_gate` itself imports). No
model is consulted on this stage.

- **The CLI identity check (`csb_is_ours`).** `~/.local/bin/csb` is not a
  symlink but a fixed three-line `/bin/sh` wrapper that install.sh writes from
  `_sandbox_gate.csb_wrapper_text`: `unset` of the launch variables above (the
  agent's shell starts the wrapper, so the launcher's clean environment never
  reaches it), then
  `exec '<python>' -I -S '<HOME>/.claude/hooks/claude_sandbox.py' "$@"`.
  `<python>` is the interpreter the launchers pick (install.sh sources the
  launcher for its `_trusted_python`). With a
  symlink, `cp x ~/.local/bin/` wrote through the link into
  `claude_sandbox.py` while every realpath comparison still matched. A
  canonical `csb` command (one that passes the host syntax check
  `host_syntax_ok` — one simple command, payloads in single quotes, no
  expansion, redirection or chaining — and parses with `parse_csb`) is
  trusted only when all of these hold, any exception counting as "no":
  - the session cwd is known (`session_cwd`, the same value the harness check
    gets; with no usable cwd at all the identity is not verified);
  - every directory on the way from `~` down to `~/.local/bin` is owned by the
    effective uid with no group or other write bit (`trusted_path` with the uid
    as owner and `~` as the top; it follows a symlink the user owns that points
    under `~`, and fails one pointing outside `~`). On Linux under umask 002
    (Ubuntu's default for users), `~/.local` and `~/.local/bin` may have been
    made group-writable (0775); then the identity is not verified, so the
    static allow does not apply: the boundary subcommands are refused and the
    other canonical `csb` commands ask (the safe side).
    `chmod g-w ~/.local ~/.local/bin` restores it;
  - `~/.local` and `~/.local/bin` themselves are real directories, not
    symlinks (`lstat`). With `~/.local/bin -> ~/repo/bin` the wrapper would
    live at `~/repo/bin/csb`, a path without `.local/bin` in it, so a write
    there is not a harness write and nothing would stop a loop from swapping
    the wrapper between the hook's check and the shell running it;
  - `~/.local/bin/csb`, opened with `O_NOFOLLOW` and checked with `fstat`, is a
    regular file owned by the effective uid, with no group or other write bit;
  - its bytes are exactly the template for some `<python>` (extracted with a
    strict pattern: an absolute path of `[A-Za-z0-9._+@/-]` only);
  - `<python>` is one of the fixed candidates, passes `trusted_path`, and is
    the very path the hook runs on (`sys.executable`, compared as a string,
    not by realpath: a venv's `bin/python` can point at the same file and still
    read its own `pyvenv.cfg` and `site-packages`). A wrapper written for
    another interpreter, or for the version-specific path behind the candidate
    (which an update of the interpreter removes), is not ours;
  - `~/.claude/hooks/claude_sandbox.py` resolves to the same file as the
    `claude_sandbox.py` beside the hook (the Codex entry looks for it where it
    finds `_bash_review_common.py`);
  - `csb` on the hook's PATH (`shutil.which`) is the same file
    (`os.path.samefile`).
- **When the identity cannot be verified.** A boundary (`create`, `net on`,
  `apply`, `image build`) is denied with a fixed text (Codex: exit 2) and any other canonical
  form asks with a fixed text. Neither falls through to the model review, and
  the summary child is never started: it would import whatever replaced the
  CLI before anyone approved it.
- **Static allow.** A canonical `exec`, `verify`, `export`, `attest`,
  `preflight`, `status`, `ls`, `rm`, `gc` or `net off` is allowed with no
  review when the identity is
  verified and the secret pre-send scan found nothing (with a secret it takes
  the usual secret ask).
- **Boundary asks.** `csb create` runs repository-defined setup with the
  network on, `csb net on` reconnects a work container, `csb apply` writes
  the patch into the host tree, and `csb image build --generic` builds the
  repository's `containers/sandbox/Dockerfile` (which the repository can
  rewrite) with the network on for the whole build. With the identity verified
  all four resolve to `ask` (Codex: exit 2), whatever the models would say and
  with or without a secret. The image build dialog shows whether the build
  context differs from HEAD (`HEAD との差: なし` or `あり (未コミットの変更 N ファイル)`, counted
  from the index, HEAD's tree and the working-tree bytes with the host git rules
  above; a difference is shown, not refused, so a human can edit and build on
  the spot), the Dockerfile's path and sha256, every instruction the build
  executes (`RUN`, `COPY`, `ADD`, `ENTRYPOINT`, `CMD`, `USER`, `ARG`, `ENV`,
  `SHELL`, `ONBUILD`, `HEALTHCHECK`, continuation lines joined into one, never
  cut), every image it pulls (the `# syntax=` frontend and each `FROM`) and
  whether each is pinned by digest, the network note, and the pinned docker's
  realpath, owner and signature, all inside the decision budget, so a Dockerfile
  whose instructions do not fit cannot be approved there. Nor can one with a
  blocker; the summary does not copy BuildKit's parser (what BuildKit does is
  inferred, not verified), so each errs towards refusing:
  - a parser directive the summary may read differently from BuildKit.
    Directives are read from the leading lines with the indentation and a
    leading BOM removed, as BuildKit does (inferred), and past blank lines and
    plain comment lines up to the first instruction, which may be further than
    BuildKit reads (so a `# escape=` after a plain comment is refused too).
    BuildKit's frontend detection drops a `#!` first line (inferred); the
    summary reads that line as a plain comment and reads on.
    These are refused: an indented `# escape=` or `# syntax=` line, a `//name=…`
    line, a directive whose name (in any case) appears twice, so the summary and BuildKit could pick different ones, an `escape` other than the backslash (another escape changes how
    continuations and quotes are read, so an instruction could hide from the
    list, such as a `RUN` swallowed by the line before it), and a file that
    reads as JSON or starts with `{` once a leading BOM and a `#!` first line
    are removed (BuildKit's frontend detection may read `{"syntax": …}`). A `# syntax=` frontend, indented or `//`, is still shown
    among the images (the first one, if there are several);
  - any `<<` that is not part of `<<<` in a `RUN`, `COPY` or `ADD` (also under
    `ONBUILD`), except a shift inside a `$((…))` that holds no whitespace and no
    expansion (`$((1<<2))`, `$((x<<y))`; `$(( 1 <<x ))` and `$((${X:- }<<x))`
    are refused, since BuildKit reads `$((` as plain characters and `<<x`
    becomes a word). It is looked for both in the instruction as listed and with
    its continuation lines joined with nothing in between, as BuildKit joins
    them. A heredoc's body, such as that of `RUN <<EOF` or `COPY <<EOF /x.sh`,
    is not listed, and `echo "a<<b"` is refused too, while `<<<word` and a `<<`
    inside `LABEL` are not;
  - a control character other than tab and newline (a carriage return only right
    before a newline), a non-ASCII whitespace character, or a U+FEFF after the
    first character: lines are split on newlines only and continuations are a
    backslash followed by spaces or tabs alone, and these characters could make
    the summary and BuildKit split lines or continuations differently. A Zs
    space (such as a full-width space in a Japanese comment) after the `#` of a
    comment line is allowed, unless the line is a parser directive or looks like
    one; one before the `#`, a U+2028 / U+2029, a control character and a later
    U+FEFF count on a comment line too;
  - a Dockerfile that cannot be read as a regular file (opened with `O_NOFOLLOW`
    and `O_NONBLOCK`, so a FIFO does not hang the preview), or a docker that
    cannot be pinned.

  The CLI does not check the Dockerfile again, so the dialog refuses (Claude:
  deny, Codex: exit 2), and a Dockerfile changed between the approval and the
  build is not detected. The repository's own `containers/sandbox/Dockerfile`
  has no blocker but does not fit today (about 2,400 characters against 1,500):
  an image build cannot be approved from the agent, and a human runs
  `csb image build --generic` in their own terminal. The summary refuses every shape listed above, which is what widening the budget presupposes; whether BuildKit reads those shapes as inferred, and whether to widen the budget, are decided in the post-landing end-to-end check with a human, which also measures how much the dialog actually shows. The approved build uses the user's docker
  configuration (buildx and other cli-plugins, credential helpers), as create
  does. Shapes that may reach a boundary but cannot be read statically ask too,
  with no summary, whether or not csb is installed:
  - a non-canonical `csb` / `claude_sandbox(.py)` call that looks like apply /
    net on / create / image build (`bash -c '…'`, `env csb …`, a chained
    `csb apply … && …`);
  - a CLI word or a subcommand word (the second word of `net on` and
    `image build` included, as in `csb image $(echo build)`) carrying a glob, a
    brace, quoting, `$` or a backslash, matched after decoding quotes, backslashes and ANSI-C `$'…'`,
    case-insensitively (the default macOS file system runs `CSB` too) and by
    stem (`claude_sandbox` with or without `.py`). A glob or brace word that
    `fnmatch` can match against `csb` / `claude_sandbox.py` counts as the CLI:
    `csb ap?ly`, `csb [a]pply`, `csb net o?`, `csb cre*`, `{csb,apply} …`,
    `~/.local/bin/cs? apply`, `python3 ~/.claude/hooks/claude_sandbox.p? apply`,
    `$'\x63sb' apply`, `cs\b apply`, `CSB apply`, `cs${x}b apply`;
  - the same behind a wrapper: `timeout`, `env`, `nohup`, `exec`, `command`,
    `nice`, `stdbuf`, `time`, `caffeinate`, `arch`, `script` (and its `-c`),
    `flock` (and its `-c`), `watch` (which joins its arguments and runs them
    with `sh -c` unless `-x`), `unbuffer`, `setsid`, `taskset`, `chrt`,
    `ionice`, `xargs` (`… | xargs csb` takes its subcommand from stdin),
    `sh | bash | zsh -c '…'`, `python3 [-I] <glob>.py`, and the command that
    `find -exec` / `-execdir` / `-ok` / `-okdir` or `fd -x` / `-X` runs;
  - a glob or brace word after the command word that may be the CLI, when the
    word after it is a boundary subcommand, as written or as a glob that may
    match one (`myrunner ~/.local/bin/cs? apply`, `… cs? ap?ly`, `… c?b n?t on`).
    Only then: an everyday glob such as `cp -r * /tmp/x` is not taken for the
    CLI;
  - bare `docker` touching the sandbox's containers, networks or labels
    (`docker exec csb-…`, `docker network connect csb-net-…`, `docker cp`,
    label spoofing). This comes before the high-risk gate, where two `ALLOW`s
    would have let it run;
  - writes or applies against the state directory
    (`git apply ~/.local/state/claude-sandbox/…`).

  This detection only ever adds asks: `fnmatch` is not shell expansion, and the
  static-allow grammar above is not loosened by it. An exception from the
  classifier is an `ask` with a fixed reason as well.

- **Doubled at the permission layer.** `permissions.ask` carries
  `Bash(csb create:*)`, `Bash(csb net on:*)`, `Bash(csb apply:*)` and
  `Bash(csb image build:*)` under the `Bash(csb:*)` allow, so the four
  boundaries reach the user even with the hook down. `permissions.deny` keeps the Edit tool off
  `~/.local/state/claude-sandbox/**` and `~/.local/bin/csb`
  (`test_config_wiring.py`).
- **The summary in the dialog.** For a canonical `csb create` / `csb apply`
  with a verified identity, a child process (the hook's own interpreter with
  `-I -S -c …`, the launch variables removed, importing the hook's own
  `claude_sandbox.py`, `preview_main`) returns structured JSON, and
  the hook renders it with fixed labels and a frame per item
  (`_sandbox_gate.render_preview`). The request goes over stdin, never argv.
  The child gets its own session, so a timeout stops it with its `git`
  children: SIGTERM first, SIGKILL only if that does not end it, 0.5 s per
  step. The whole stage has a 5 s budget, the stop steps included.
  - Decision items come first and are never cut. For apply: the identity, the
    blockers, every RISKY category with its count (all harness paths listed),
    the secret scan state, the verify state with the argv it ran ("only that
    this argv exited 0"), and the attests ("self-reported"). For create: the
    repository and HEAD, the snapshot rule (tracked files with their working
    tree contents, uncommitted changes included, untracked and ignored files
    excluded), the image and when it is pinned, the Dockerfile and build
    context, every setup step in full with its origin, the network scope (only
    during setup), and "no host mount". What the preview does not check
    (uncommitted changes, the untracked count, the secret scan, LFS, the host
    base tree, and for apply the filter attributes of the target paths) is
    shown as unchecked, never as "no blockers".
  - The decision part is budgeted at about 1500 characters and 25 lines. If it
    does not fit, the command is denied (Codex: exit 2) with a fixed text: a
    short dialog cannot approve what it cannot show. A setup is never shown
    head-and-tail; one too long to show asks for a shorter `--setup` or
    devcontainer command instead. How much of the reason Claude Code's dialog
    displays is not documented and has to be measured end to end.
  - Only the file list may be cut, at 4000 characters overall. The omission
    mark (`… ファイル欄の残り N 行を省いた。全文は csb preflight ID --patch SHA で見る`)
    is added after the cut, so it is never cut itself. The manual steps for a
    harness patch sit outside the decision budget and are not cut either.
  - Strings from the child are sanitised with `display_text` item by item: besides
    control, bidi and zero-width characters, it escapes every space other than
    U+0020 (Zs), format characters (Cf), the Hangul and Braille fillers, tag
    characters and variation selectors, which could otherwise shift a setup
    line or fake an empty one on screen. When
    no summary can be built, only a fixed phrase is appended, never the child's
    output or an exception message: `（要約を作れなかった: <語>）`, the word
    being one of `時間切れ`, `拒否`, `エラー`, `作業ディレクトリ不明` and
    `秘密を検出`. With a secret in the command no summary is built, because the
    create plan would show the `--setup` value. The summary log keeps the
    fixed reason plus `(summary=<state>)`.
- **What the preview and the preflight touch.** The create preview, the apply
  preview and the statically allowed `csb preflight` never run
  `git stash create` and never stat the index or the working tree through
  git. They read refs, the index file (`ls-files -s`), config and attributes
  (`check-attr`, which runs no filter), and read working-tree files directly
  (`O_NOFOLLOW`, never through a symlinked directory). The host base tree
  check is left to the real apply.
- **Harness patches are refused.** `csb apply` refuses any patch that touches
  the harness (RISKY `harness`, the same set as `_is_harness_target`), even
  with every review passing. The refusal, the preflight and the dialog give
  the steps a human runs instead, with absolute paths: first
  `shasum -a 256 <patch>`, whose output must start with the patch's sha (the
  patch in the state directory could be swapped between reading the dialog and
  applying by hand), then `git -C <repo> apply --check <patch>`, then
  `git -C <repo> apply <patch>`.
- **Host git in the real create and apply.** Every host git call is the
  root-owned `/usr/bin/git` (checked with `trusted_path`; PATH's git, such as
  Homebrew's, is not used), runs with `PATH=/usr/bin:/bin:/usr/sbin:/sbin` and
  an allowlisted environment (`HOME`, `USER`, `LOGNAME`, `LANG`, `LC_*`,
  `TMPDIR`, `XDG_CONFIG_HOME` and the git config file locations; nothing like
  `GIT_EXEC_PATH`, `GIT_SSH*`, `GIT_CONFIG_COUNT`, `DYLD_*` or the xcrun
  variables gets through), and carries
  `-c core.fsmonitor=false -c core.hooksPath=/dev/null -c protocol.allow=never`
  and `GIT_OPTIONAL_LOCKS=0`, which stops optional locks only and does not make
  git read-only. docker is the absolute path pinned at create, with the same
  environment and its own directory after the fixed PATH (Docker.app keeps
  its credential helpers there), and an empty private `--config` except for the
  create's context, pull and build (see the known gaps). The snapshot no longer uses `git stash create`, which
  rewrites the index (its post-index-change hook included) and runs clean
  filters. The tracked files' working-tree bytes are hashed with
  `hash-object --no-filters` into a throwaway index (`GIT_INDEX_FILE`), so no
  filter command ever runs. Because those bytes skip the filter, a repository
  with a filter attribute on any tracked path is refused, whatever the driver
  (git-crypt, transcrypt, LFS, …), with the message
  `filter 属性のあるリポジトリは非対応`. With git-crypt
  unlocked, the working tree holds plaintext, and the snapshot would carry it
  into the networked setup stage. The check uses `check-attr`, twice (the
  working tree, then `--cached`, since by default the working-tree
  `.gitattributes` hides the index copy), so the attribute counts wherever it
  is set (any `.gitattributes`, untracked ones and the index copy included,
  `info/attributes`, `core.attributesFile` or its default
  `$XDG_CONFIG_HOME/git/attributes`, macros). It runs before any file is hashed
  (`hash-object -w --no-filters` would write the plaintext into the host's
  object store), and so before any build context or network. The create preview shows it as a
  blocker; apply's base tree goes through the same function. Also:
  - a partial clone (`extensions.partialClone`, a promisor remote) is refused;
  - a plan file the plan reads but git does not track (untracked or ignored,
    such as a local `.devcontainer/devcontainer.json`) refuses create, since
    the snapshot would not match what the dialog showed;
  - a devcontainer definition whose Dockerfile lies outside its build context
    refuses create (the plan says so, so the dialog's summary shows it); it
    does not fall back to the generic image, which the definition did not ask
    for;
  - the secret scan completes before any transfer, build or networked setup;
  - apply refuses a patch whose target paths carry a filter attribute (`git
apply` would run the filter), checked with `check-attr`, which runs none.

- **Known gaps.**
  - A model running as the same uid can forge the state directory, including
    the verify records and the attests. Attest is a record against missed or
    reused reviews, not forgery protection.
  - The CLI's TTY prompt in `csb apply` does not prove a human: a program can
    create and drive a PTY.
  - Obfuscation detection cannot be complete. A command word that is only a
    variable, a renamed script, or a call from inside Python is not
    recognised, and `fnmatch` is not shell expansion. Because apply refuses
    harness patches, an obfuscated `csb apply` that slips through can write no
    more than the Edit tool already can.
  - The Edit / Write tools can copy a patch's content into the host tree by
    hand. That path never runs `csb apply`, so neither the boundary ask nor
    apply's checks see it; only the human reviewing those edits does.
  - The boundary ask only covers a direct invocation. A boundary the command
    line never spells out escapes it: decoded at run time
    (`echo … | base64 -d | sh`), written to a script first (`bash run.sh`), run
    through a copy of the CLI (`cp ~/.local/bin/csb x; ./x apply …`), or put in
    a package script (`npm test`). The CLI itself has no confirmation for
    create and only a TTY prompt for apply, so such a call rests on the model
    review and on `permissions.ask`, which matches the plain prefix only. This
    is the scope the user chose: create and net on are detected only and stay
    inside the container; apply always refuses harness patches, and an
    ordinary file it could write is one the Edit tool can write as well.
  - The image build dialog is advisory in the same way (a fixed line says so):
    the CLI reads the Dockerfile again when it builds, and nothing ties the
    approved summary (its sha256, say) to the build that runs.
  - What the human approved in the create dialog and what the create run
    plans again are not tied together (the create dialog says so in a fixed
    line outside the decision budget:
    `この要約は参考表示です。承認の後に CLI が計画を立て直すため、表示と実行の一致は保証されません`). The CLI re-plans from the working tree
    after the approval, so a plan file changed in between (for example by a
    background job started earlier) runs a setup the dialog never showed. A
    plan digest carried by the command (`csb create --plan <digest>`) would
    tie them; it is not implemented.
  - The identity check covers only what the hook process sees. The Bash
    tool's shell layer (functions, aliases, PATH) is outside it: the shell may
    resolve `csb` differently, so the check proves what the hook would run,
    not necessarily what the shell runs. In zsh a function can replace
    `command`, `builtin` and `exec`, and even a name with a slash in it such as
    `/abs/path/csb` (shown in practice). The answer for that layer is to
    protect the shell's start-up files: `permissions.ask` has Edit rules for
    `.zshrc`, `.zshenv`, `.zprofile`, `.zlogin`, `.zlogout`, `.bashrc`,
    `.bash_profile`, `.bash_login`, `.profile` and `~/.oh-my-zsh/**` (`.zshrc`
    reads `$ZSH/oh-my-zsh.sh`, which reads `custom/` and `plugins/`), plus what
    zsh loads besides them: compiled `*.zwc` files (`source x` reads `x.zwc`
    when it is newer), the completion dump `.zcompdump*` that `compinit`
    loads, and `~/.docker/completions/**` (Docker Desktop puts it in `fpath`).
    The same files are bash-review harness paths for Bash writes. What remains:
    a shell snapshot already taken (`~/.claude/shell-snapshots/`) keeps what it
    captured however the start-up files are protected, and executables in
    user-writable places that `.zshrc` loads or runs are covered only where
    listed here (the `fzf` that `brew upgrade` replaces is not, see the
    Homebrew item below).
  - Executables in user-writable places can be replaced: `Docker.app`,
    `codex` under `~/.npm-global`, and so on. This class predates csb and does
    not come from it. csb's host side uses the root-owned `/usr/bin/git`. docker
    is not taken from the inherited PATH (which may put `~/.cargo/bin` first):
    it is the first of fixed places (`HOST_PATH`, then `/usr/local/bin`, and on
    macOS `/opt/homebrew/bin` and `/Applications/Docker.app/Contents/Resources/bin`),
    its realpath must sit under a known root (`/usr/`, `/bin/`, `/sbin/`, and on
    macOS `/opt/homebrew/` and `/Applications/Docker.app/`), and the chain must
    pass `trusted_path` with root or the user as owner (macOS's admin group may
    write, as it can write `/Applications` and the Homebrew prefix). The create
    dialog shows the pinned realpath, its owner and the signature result in
    full, and every later use checks the pinned path again.
  - A docker under `/Applications/Docker.app/` is owned by the user, so the same
    uid can rewrite it; and since the admin group may write `/Applications`, an
    admin user can also rename the whole bundle aside and put another in its
    place. Neither shows in the owner or mode checks. Such a docker must
    therefore carry Docker Inc's signature: at create and at every later use,
    `/usr/bin/codesign --verify --strict` with the requirement
    `anchor apple generic and certificate leaf[subject.OU] = "9BNSXJN65R" and identifier "docker"`
    (the identifier matters: `Docker.app/Contents/Resources/bin` also holds
    other Docker-signed binaries, `kubectl` and `docker-credential-desktop`
    among them, and a copy of one named `docker` passes the Team ID alone;
    when the full requirement fails, a second check with the Team ID alone
    tells the two apart: a binary that carries Docker's signature but another
    identifier is refused with
    `Docker の署名だが identifier が docker でない (Docker Desktop の更新で変わった可能性。README を参照)`.
    If a genuine Docker Desktop update changed the identifier, a human checks
    it with `codesign -dv /Applications/Docker.app/Contents/Resources/bin/docker`
    and corrects `_DOCKER_REQUIREMENT` in `claude_sandbox.py`; until then csb
    refuses the docker;
    codesign itself must pass `trusted_path` as root's; it runs with
    `PATH=/usr/bin:/bin:/usr/sbin:/sbin` only and a 10 s timeout; one run takes
    about 0.1 s here, and a pass is remembered inside one process for the same
    file state, keyed by device, inode, size, mtime and ctime). A rewritten or
    swapped binary fails and csb refuses; the dialog shows
    `署名: Docker Inc (9BNSXJN65R) 確認済み`. What remains: the window between
    that check and docker actually starting (a swap in between runs the new
    file), the rest of the bundle (helpers and plugins are not checked), and a
    Homebrew docker, which carries no Docker Inc signature and is not checked
    (writes into the Homebrew prefix ask, Edit and Bash). Linux's candidates sit
    under root. Making `/Applications/Docker.app/**` an Edit ask and a harness
    path would be a permission decision and is not done. Same class of gap as
    `Bash(docker:*)`.
  - csb runs docker with `--config` set to a new empty 0700 directory, made
    for each call under `<state dir>/docker-config.d` (a real directory owned
    by the user, mode 0700, checked every time) and removed when the call
    ends, so nothing placed there beforehand is read and nothing docker writes
    (buildx state, say) reaches the next call. `~/.docker/config.json` and
    `~/.docker/cli-plugins`, which Bash can write without an ask, are not read
    on the statically allowed paths (`exec`, `verify`, `export`, `status`,
    `ls`, `rm`, `gc`, `net off`, and every inspect; none of them uses buildx or
    compose). Only the approved boundaries read the user's docker
    configuration: create, for the context that pins the endpoint and for pull
    / build, and image build, for the context and the build (credential
    helpers, buildx and other cli-plugins); that configuration is outside the
    protection. An `rm` or `gc` with no metadata pins its endpoint with the
    empty configuration, i.e. the default context `unix:///var/run/docker.sock`:
    Docker Desktop links it to the user's socket only when its default-socket
    setting is on, and without it those commands fail and stop. When the
    docker the metadata pinned fails its re-check (an update replaced it, say),
    `rm` and `gc` pick the docker binary again from the fixed places (with the
    same trust and signature checks) but keep the recorded endpoint, where the
    resources were made (the empty configuration's default context is often a
    different daemon), and still remove only what carries the sandbox's id
    label. A listing (`ps`, `network ls`, `images`) that
    fails counts as a failure, so a docker that cannot be reached never leaves
    resources behind a removed state. `ls` / `status` show a refused docker as
    `照合: …` with the network `不明 (固定した docker が検査に落ちた)`, `gc` warns about a sandbox it cannot
    remove and goes on, and an unexpected error (a file that cannot be
    written, a child that times out) is `csb: エラー: …` with exit 1, never a
    traceback.
  - Homebrew's prefix (`/opt/homebrew`, `/home/linuxbrew/.linuxbrew`) holds
    the git, fzf, bash and python the shell finds first on PATH. A direct write
    into it asks (`Edit(//opt/homebrew/**)` and
    `Edit(//home/linuxbrew/.linuxbrew/**)` in `permissions.ask`, and harness
    paths for Bash writes), but what brew installs there is not guarded: a
    `brew install` / `brew upgrade` or a tap's formula still puts its own
    contents in the prefix. Intel Macs' `/usr/local` is left outside the
    protection because it is too broad (much else lives there), and stays a
    gap.
  - `/usr/bin/python3` on macOS is an xcrun shim: it runs the python3 of the
    developer directory that `xcode-select` chose (`/var/db/xcode_select_link`,
    owned by root). The Command Line Tools candidate comes first, so it is used
    only when those are missing; then the trust in the shim is as good as the
    selected developer directory (an `Xcode.app` the user owns can be
    rewritten), and since the hook's `sys.executable` is then that directory's
    python3 rather than a candidate, csb always fails closed there: boundaries
    are denied and every other csb call asks.
  - What the dialog showed and what the real create snapshots can differ if a
    tracked plan file changes in between. Untracked plan files are refused,
    but a re-approval on a changed tracked file is not enforced.
  - The filter-attribute check before `git apply` reads the host's current
    attributes. A `.gitattributes` that the patch itself adds or changes is not
    taken into account (`.gitattributes` is RISKY `hooks`, so it needs the
    security review, but it is not refused).
  - A `csb net on --minutes N` window ends at the next csb call, not on the
    clock: every subcommand first disconnects the windows past their end (the
    containers pane's 10 s poll of `csb status --json` happens to do it while
    the pane is open), and a busy sandbox (its lock held) is left for the next
    call. A `csb exec` already running keeps the network past the end until
    then; nothing disconnects it in between (there is deliberately no resident
    process). `csb ls` / `status` show the actual connection, read from the
    work container's networks, not the recorded window: a window past its end
    that is still connected shows `on (期限切れ。次の csb の呼び出しで切断)`
    (`--json`: `net_connected`, `net_expired`; `net_on_until` keeps its meaning
    of a window still open). A container that fails its check, or that is
    attached to a network other than the sandbox's (`bridge`, `host`,
    `container:…`), shows `不明 (照合に通らない)` and `net_connected: null`,
    never `off`; a window past its end is the one check failure still shown as
    connected. The containers pane reads the same fields.
    `不明 (docker に届かない)` (Docker is not reachable) and
    `不明 (固定した docker が検査に落ちた)` (the pinned docker fails its re-check,
    so csb does not call it) are the same unknown state and also have
    `net_connected: null`; `--json` keeps one shape for all three, and its
    `check` carries the cause. An unknown state may well be a connected
    one, so the rule for the agent is to treat it as connected and run
    `csb net off ID`. When Docker is not reachable, `csb net off` itself fails
    (exit 1), so if it fails, or the state is still unknown, the agent tells the
    user. (`docker-patterns/references/sandbox.md` says the same.)
  - Codex's PreToolUse hooks fail open on a timeout. Codex 0.171.0, the version
    on this machine, has no setting that blocks on a hook failure (`onFailure`
    arrived on openai/codex main after that release), so the template states
    `"timeout": 600` and cannot do more; see `.codex/hooks/README.md`.
  - Exit 2 has two meanings: `csb` refusing (argument, contract or check
    mismatch) and a command inside the container exiting 2 (`exec` and `verify`
    pass the inner code through). Tell them apart by stderr: a refusal starts
    with `csb: 拒否:`.
  - Longer term, the real fix is to separate the development repository from
    the live harness: promote a reviewed set into a location the agent cannot
    write, instead of `~/.claude` linking straight into the checkout it edits.

**Claude Code's own sandbox is not used.** `settings.json` no longer has a
`sandbox` key, so the feature is off (its default): the enforcement boundary is
`permissions.deny` plus these hooks, and isolation for work that must not touch
the host or the network is the job of the container sandbox CLI above.
`test_config_wiring.py::test_no_inert_sandbox_block` keeps a disabled block
from coming back: while the feature is off, keys under `sandbox`
(`network.allowedDomains`, `excludedCommands`) are never consulted and would
only read as protection.

**Log handling.** The detail log holds the full command and the reviewers'
answers, and `/tmp` is shared, so the entry points treat its tree as untrusted
input and never let logging decide a verdict:

- Before any decision, `prepare_detail_log_dir` walks from the managed root
  (`/tmp/claude_hooks`, Codex: `/tmp/codex_hooks`) down to the leaf, one level
  at a time (`mkdir`, then `lstat`, then `chmod`). Every level must be a real
  directory (not a symlink), owned by the effective uid, and exactly `0700`. The
  walk never looks above the root (on macOS `/tmp` is itself a symlink), and
  stops at the first level that fails, so nothing is created below it. A
  failure — a root made by another uid (`sudo claude`, a container), a symlink
  planted in advance, a `chmod` that does not stick — disables **detail**
  logging for that run only; the verdict is computed as usual. The summary log
  then gets an extra line, `NOTE | logging | - | detail logging disabled:
<why>`, so the `bash-review-log` mod's full-command lookup does not stop
  silently. It is written only when none of the last 100 lines of the summary
  log is already such a note (only the tail of the file is read), so a tree
  that stays unusable costs one line per ~100 decisions instead of halving the
  500-line summary. It carries no command text: the secret scan has not run
  yet. `NOTE` is not an allow / ask / deny, so a reader that counts decisions
  files it under "other".
- Pruning (the oldest files beyond 1000) runs only after that check passes, and
  counts and deletes only the hook's own names, `bash_cmd_*.log`. An entry that
  cannot be deleted (a directory with such a name, an immutable file) is skipped
  and pruning goes on to the next one, so it cannot halt the clean-up for good.
  A failure to list the directory is ignored: the logs can still be written.
- Every logging write is best-effort — the detail log (also in the size-limit
  branch), the summary log's directory creation, append and rotation. An
  `OSError` (or a character that cannot be encoded to UTF-8) there is swallowed
  and nothing else is: an exception from parsing, the secret scan or a review
  still fails closed (ask; Codex: exit 2). The decision is also settled before
  the logging runs in every branch, so a logging bug cannot flip an allow into
  a block or replace a block's reason with "hook error".
- The detail file is opened `O_NOFOLLOW` with mode 0600.

Residual risk: the checks are path-based, not directory-fd based
(`lstat` then `chmod` / `open` by path), so a swap between the check and the
use is a TOCTOU window. It is narrow in practice — a level that passes is a
directory we own, inside a root we own that is `0700`, and `/tmp` is sticky, so
only the same uid can replace it — but it is not closed. Closing it would mean
opening each level with `O_DIRECTORY | O_NOFOLLOW` and working through
`fstat` / `fchmod` / `*at` calls.

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
  positive only costs an `ask`. A known-format token ends at the first ASCII
  word character that cannot belong to it, so one glued to Japanese prose
  (`AKIA…です`) is still caught; kana / kanji are not word characters here
  (a regex `\b` would count them as such and miss the token). Nothing the old
  `\b` matched is lost: the two formats whose body may contain `-` keep it.
  `sk-…` is greedy, so a trailing `-` is simply part of its body; `AIza` + 35
  characters accepts either end condition (no ASCII word character follows, or
  the old `\b`), so `AIza<35>-prod`, `…-`, `…--` and `?key=AIza<35>-1` are still
  caught, as is a key whose last character is `-` followed by a space.
- Safe-skipped `git status` / `git log` / `git diff` / … assume a **trusted**
  repository. A command-running setting written _through the agent_
  (`git config core.fsmonitor …`, `git -c …`, `GIT_CONFIG_*`) now reaches the
  high-risk gate (step 3). A `.git/config` that was already planted (a cloned
  or untrusted repository, or one edited outside the Bash tool) is still out
  of scope: verifying it on every call would defeat the point of the skip.
  See the inline comments in `_bash_review_common.py`.
- **Network egress from inside a container** is accepted, not blocked.
  `docker run alpine wget …` or `docker compose exec web curl …` sends what
  the container can see. The `curl` / `wget` / `nc` denials cover only the
  host's own executables, and only the docker shapes listed in step 3 (mounts,
  namespaces, daemon) are gated. Isolation for work that must not talk to the
  network is the sandbox CLI's job (`csb`, network off while working).

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
