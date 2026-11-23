# Sandbox verification with `csb`

`csb` runs a throwaway verification in a container and brings the result back as a reviewed git
patch. The container has **no mount of the host**, so code running in it cannot write host files; the
host tree changes when a reviewed patch is applied, by `csb apply` after the user approves it (or by
the user by hand). That is the design, not a guarantee: the known gaps are listed at the end. This
file is the usage guide. The facts behind it live in `containers/sandbox/README.md` (the generic
image) and `.claude/hooks/README.md` ("The sandbox CLI stage"); the code is
`.claude/hooks/claude_sandbox.py` (the CLI) and `.claude/hooks/_sandbox_gate.py` (the contract). If
this file and the CLI disagree, the CLI is right.

## When to use it — and when to use a worktree

- **Docker is usable**: run disposable verification here. That means installing and running code you
  have not reviewed, trying a risky change, or running a test suite you do not want near the host.
  Check with `docker version --format '{{.Server.Version}}'` through the Bash tool with `timeout`
  set to 15000 ms. If it times out or exits non-zero, Docker is not usable. `csb` only works against
  a daemon on a `unix://` socket, and `create` refuses any other context.
- **Docker is not usable, or `csb` is not installed** (`~/.local/bin/csb` is missing; `csb` is
  installed by `install.sh` as a fixed wrapper around `claude_sandbox.py`): say so. For ordinary
  verification, such as testing a change you wrote yourself, use a git worktree as before. A worktree
  shares the host's user, network and secrets, though, so if the point is to run unreviewed code or to
  install dependencies, ask the user before switching to it. Either way, the worktree is the
  fallback, not a peer; ask the user to run `./install.sh` or start Docker if they want the sandbox.
- **The user refuses a boundary** (`create`, `net on`, `apply`): stop there and report. Do not switch
  to a worktree or to bare `docker` on your own; ask the user first.
- Under Codex this skill is shared (`.codex/skills/docker-patterns` links here), but Codex's hook
  cannot ask: a boundary command (`create`, `net on`, `apply`, `image build`) is blocked with exit 2.
  Gemini CLI reads it too (`.gemini/GEMINI.md` points at the docker-patterns skill) and has no
  bash-review hook at all, so nothing there stops a boundary. Under Codex and Gemini alike, do not run
  a boundary command; hand it to the human to run in their own terminal.

## Subcommands

"Static allow" means the bash-review hook lets the command run with no prompt. It does so only when
the command is in canonical form (see [Quoting](#quoting-the-payload)), the hook has verified that
`~/.local/bin/csb` is the wrapper `install.sh` wrote, and the secret pre-send scan found nothing (a
secret takes the usual secret prompt). If the wrapper cannot be verified, a boundary is denied and
any other canonical form asks.

A form that is **not** canonical gets no static allow, and that is not the same as "asks". One that
looks like `create`, `net on`, `apply` or `image build` (behind a wrapper, an expansion, a chain or
a quote) asks, without the summary. Any other non-canonical `csb` command, such as a chained
`csb exec … && csb export …` or `sh -c "cd $PKG && …"`, goes to the ordinary model review, which
does not guarantee that a human sees it. Write every call in canonical form.

| Subcommand                                                                     | Confirmation                     | What it does                                                                                                                                     |
| ------------------------------------------------------------------------------ | -------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| `create [--image auto\|generic\|REF] [--setup CMD]`                            | **ask** (summary of the plan)    | Snapshots the repository, builds or pulls the image, runs the setup steps with the network on, then keeps a work container with the network off. |
| `exec ID [-w ABS] [-e NAME=VAL]… -- ARGV…`                                     | static allow                     | Runs ARGV in the work container (`/work`, the image's user). Exits with the inner command's code.                                                |
| `export ID`                                                                    | static allow                     | Turns the changes in `/work` into a patch. Prints its sha256, a summary and the reviews it will need.                                            |
| `verify ID --patch SHA -- ARGV…`                                               | static allow                     | Starts a fresh container from the setup image, applies the patch to the pristine snapshot, runs ARGV, and records the exit code against the sha. |
| `attest ID --patch SHA --review NAME=pass\|fail [--ref PATH] [--unresolved N]` | static allow                     | Records that a review happened, against the patch's sha. `--ref` stores the sha256 of a review file.                                             |
| `preflight ID --patch SHA`                                                     | static allow                     | Prints what `apply` would show and every reason it would refuse. Does not touch the host tree. Exits 1 when something blocks.                    |
| `status [ID] [--json]`, `ls [--json]`                                          | static allow                     | State of the container, its network, and per patch the verify result and review states.                                                          |
| `rm ID`                                                                        | static allow                     | Removes the sandbox's containers, network, images and state.                                                                                     |
| `gc [--older-than Nd]`                                                         | static allow                     | Removes sandboxes older than N days (default 7) and orphaned labelled Docker resources.                                                          |
| `net off ID`                                                                   | static allow                     | Disconnects the work container from the network.                                                                                                 |
| `net on ID [--minutes N]`                                                      | **ask**                          | Reconnects the work container for N minutes (1–120, default 15).                                                                                 |
| `apply ID --patch SHA`                                                         | **ask** (summary with the risks) | Applies the patch to the host repository. Refuses unless every condition under [apply](#apply) holds.                                            |
| `image build --generic`                                                        | **denied** to an agent           | Builds the generic image `claude-sandbox-generic:1` from `containers/sandbox/Dockerfile`. The user runs it; see [below](#the-generic-image).     |

`ID` is 12 lowercase hex digits, printed as the last line of `create` (`sandbox: <ID>`). Option
values are separate words: `--minutes 30`, never `--minutes=30`.

## The usual flow

1. **create.** `csb create` (add `--setup 'npm ci'` or `--image REF` when the defaults are wrong).
   `--image auto` uses the repository's devcontainer definition (`image` or a Dockerfile build) and
   falls back to the generic image when there is none; a definition whose Dockerfile lies outside
   its build context refuses create instead. Setup steps come from `--setup`, else the
   devcontainer's `onCreateCommand` / `updateContentCommand` / `postCreateCommand`, else a lockfile
   install. The approval dialog shows the plan; the CLI plans again after approval, so treat the
   dialog as a summary.
2. **exec.** Work in the container: `csb exec ID -- sh -c 'npm test'`. Edit files with `exec` too
   (the host tree is out of reach). The network is off; see [net on](#net-on).
3. **export.** `csb export ID` prints the patch's sha256 and what it touches, and names the reviews
   it needs. The file is `~/.local/state/claude-sandbox/ID/patches/SHA.patch`; reviewers read it
   there. If more `exec` work changes the content, the next `export` has a **new** sha, and
   everything recorded for the old sha (verify, attests) no longer counts.
4. **verify.** `csb verify ID --patch SHA -- npm test`. This runs in a fresh container with only the
   patch applied, so a pass means the patch alone is enough.
5. **review.** Review the patch, not the sandbox:
   - the `code-reviewer` agent;
   - Codex and the advisor as a pair, run by the main session as `~/.claude/rules/second-opinion.md`
     and the codex-consultation skill describe (Codex in the background, then the advisor);
   - the `security-reviewer` agent when `export` / `preflight` lists it (below).
6. **attest** each review under its exact name (`code-reviewer`, `codex`, `advisor`,
   `security-reviewer`), once it is done:

   ```bash
   csb attest 3f9a1c0b2d4e --patch SHA --review code-reviewer=pass
   csb attest 3f9a1c0b2d4e --patch SHA --review codex=pass --unresolved 1 --ref /abs/path/review.md
   ```

   `--unresolved N` counts findings left open and `--ref PATH` stores the sha256 of the written
   review. A `fail`, or an `--unresolved` above 0, blocks `apply`. For each name the latest attest for
   that sha is the one that counts. See [What to record](#what-to-record) for the rules.

7. **preflight, then apply.** Run `csb preflight ID --patch SHA`; when it lists no blocker, ask for
   `csb apply ID --patch SHA`. `preflight` does not look at the host tree, so it cannot see the two
   checks `apply` adds (the base tree and the filter attributes). After a successful apply, run
   `csb rm ID`.

Required reviews: `code-reviewer`, `codex` and `advisor` always, plus `security-reviewer` when the
patch touches a RISKY path (harness, CI, dependency manifests and lockfiles, git hooks and other
auto-run config, container definitions), when the added lines trip the secret scan, or when the
patch was too large to analyse. Attests are self-reported records that catch a missed or reused
review; they do not prove the review was good.

### What to record

`attest` is statically allowed, and nothing checks that the review happened. So the record is only
as honest as you are:

- Record the result that actually came back, for a review that actually ran. Never record a review
  you did not run, and never one that ran against a different patch.
- If you disagree with a finding, leave it open with `--unresolved N` and tell the user. The opinions
  are surfaced, not settled quietly (`~/.claude/rules/second-opinion.md`, "Conflicting advice").
- **If `advisor` or `codex` cannot run** (the second-opinion pair then includes Gemini), the required
  review name cannot be recorded, and the other names do not stand in for it. Do not go on to
  `apply`: tell the user and stop. Never record a Gemini result as `advisor` or `codex`.

## Quoting the payload

Everything after `--` is run inside the container, and the host shell reads it first. A command gets
the static allow only when it is one simple command: no `;` `&` `|` `<` `>`, no `$`, backtick or
backslash, no `~`, glob or brace, no newline or comment, outside single quotes. So **put the payload
in single quotes**:

These run with no prompt (the second needs no shell at all):

```bash
csb exec 3f9a1c0b2d4e -- sh -c 'cd packages/app && npm test -- --run'
csb exec 3f9a1c0b2d4e -w /work/packages/app -- npm test
```

These get no static allow, the first because of the `$` inside double quotes and the second because it
chains two commands. Neither names a boundary, so neither is a sure prompt: they go to the ordinary
model review, and no human is guaranteed to see them.

```bash
csb exec 3f9a1c0b2d4e -- sh -c "cd $PKG && npm test"
csb exec 3f9a1c0b2d4e -- npm test && csb export 3f9a1c0b2d4e
```

Run one canonical `csb` command per Bash call. What happens to a form the hook cannot read as one
canonical command is described under [Subcommands](#subcommands); the gaps in that detection are in
`.claude/hooks/README.md` (**Known gaps**).

## net on

The work container has no network. `csb net on ID --minutes N` connects it for a limited time
(default 15 minutes, at most 120); `csb net off ID` ends it early. Turn it on only for what needs it
(fetching a dependency the setup step did not) and off right after.

**The expiry is not on the clock.** Every `csb` call first disconnects the windows that have passed
their end. Until the next `csb` call, the network stays connected, and an `exec` that is still
running keeps it past the end. `csb ls` / `status` show the real connection:
`on (期限切れ。次の csb の呼び出しで切断)` means the window ended but nothing has disconnected it
yet. Do not rely on the window ending by itself; run `csb net off ID`.

**`不明` is not `off`.** When the real connection cannot be read, `ls` / `status` print
`不明 (docker に届かない)` (Docker is not reachable) or `不明 (照合に通らない)` (the container fails
its check, or is attached to a network other than the sandbox's), and `--json` has
`net_connected: null`. Treat it as connected and run `csb net off ID`. When Docker is not reachable,
`csb net off` itself fails (exit 1). If it fails, or the state is still `不明`, tell the user.

## Exit codes

| Code | Meaning                                                                                          | stderr starts with  |
| ---- | ------------------------------------------------------------------------------------------------ | ------------------- |
| 0    | Success.                                                                                         |                     |
| 1    | A runtime error (docker, git, a file, a child process that timed out).                           | `csb: エラー:`      |
| 1    | `preflight` listed a blocker. The list is on stdout; nothing is written to stderr.               | (nothing)           |
| 1    | `apply`'s [y/N] prompt was declined; nothing was applied.                                        | `csb: 中止:`        |
| 2    | `csb` **refused**: an argument, a contract or a check did not hold.                              | `csb: 拒否:`        |
| 2    | The arguments did not parse; the usage follows.                                                  | `csb: 引数が不正`   |
| 2    | **The command inside the container** exited 2 (`exec` and `verify` pass the inner code through). | (the command's own) |
| 130  | Interrupted.                                                                                     | `csb: 中断した`     |

Exit 2 therefore has two meanings. Tell them apart by stderr: a refusal begins with `csb: 拒否:`
(bad arguments with `csb: 引数が不正`); anything else with exit 2 came from the command you ran.
`verify` ends with `csb: verify PASS (exit 0)` or `csb: verify FAIL (exit N)`; `status` and
`preflight` can also report `MISSING` (no record) and `STALE` (a record for another patch or base).

## What is not supported

- **Submodules and Git LFS** are refused at `create`, as are a filter attribute on any tracked path
  (git-crypt, LFS and the like), a partial clone, an unresolved merge, a repository with no commit,
  an image that declares a `VOLUME`, and secret-looking values in the snapshot.
- **The snapshot is the tracked files with their working-tree contents**, uncommitted edits included.
  Untracked and ignored files are left out, and only their count is shown. A file the plan reads
  (a local `.devcontainer/devcontainer.json`, say) that is untracked makes `create` refuse.
- **If `/work` has drifted from the snapshot after setup, `create` aborts** (exit 1, "`setup: /work`
  がスナップショットから変わった …") and removes what it made. Put generated files in `.gitignore`.
- **`apply` refuses when the host's base tree no longer matches** the one snapshotted at `create`:
  the contents of the host's tracked files must be exactly as they were. Do not edit them, pull, or
  switch branches in the host repository between `create` and `apply`. If they moved, `rm` the
  sandbox and `create` again; there is no way to re-base it.
- **Patches** over 64 MiB are not written by `export`, and one over 4 MiB cannot be analysed, so
  `apply` refuses it. Split the work.

## apply

`apply` writes into the host repository, so it always asks, and its dialog carries a risk summary:
the blockers, each RISKY category with its count, the secret scan, the verify result with the argv it
ran, and the attests. `apply` refuses unless all of these hold:

- `verify` is `PASS` for this exact sha;
- every required review is `pass` with no unresolved findings;
- the host base tree matches (above);
- the patch is non-empty, analysable, has no invalid path and no submodule change;
- it touches no filter-attribute path;
- it does **not touch the harness**.

**A patch that touches the harness is always refused**, however complete the reviews are. The
harness is `.claude/`, `.codex/` and `.gemini/` (at any depth), `CLAUDE.md`, `AGENTS.md`,
`GEMINI.md`, `.mcp.json` and the like. `preflight`, the refusal and the dialog print the steps for a
**human** to run by hand, with absolute paths: `shasum -a 256 <patch>` (the output must start with the
sha), `git -C <repo> apply --check <patch>`, then `git -C <repo> apply <patch>`. Do not run them
yourself, and do not copy the patch's content into the host tree with the Edit or Write tool: that
path skips every check `csb apply` makes (see [Do not](#do-not)).

When `apply` runs on a terminal it also asks `[y/N]`. That prompt is not proof of a human, since a
program can drive a terminal; it is the human's approval of the dialog that counts.

## The generic image

`csb create` falls back to `claude-sandbox-generic:1` when the repository has no usable devcontainer
definition (a definition whose Dockerfile lies outside its build context refuses create instead),
and refuses with "汎用イメージ … が無い。先に `csb image build --generic` を実行する" if
the image is not built.

**`csb image build --generic` is denied when an agent runs it; the user runs it in their own
terminal.** The command builds `containers/sandbox/Dockerfile` with the network on, so its dialog
would have to show every instruction. It never can: the real Dockerfile does not fit in the dialog's
budget, and a Dockerfile with a blocker (a heredoc, say) is refused too. The hook therefore denies the
command instead of showing a partial view, and no one is left to answer it. Do not wait for it, retry
it or build the image another way. When the image is missing, stop and ask the user to run it.
Updating the base digests is in `containers/sandbox/README.md`.

## Do not

- Operate a sandbox's containers, networks or images with bare `docker`. The hook looks for
  `exec`, `cp`, `network`, `commit`, `export`, `attach`, `rename` and `update` that name a `csb-…`
  container or network, for `run` and `create` that use a sandbox's name or label, and for `build`
  and `buildx` that use its label (a build is looked at only for the label), and asks about those. It does not look for `stop`, `kill`, `start`, `logs` or a container named by its ID.
  That is a gap, not permission: use `csb exec`, `net on`, `net off` and `rm`.
- Edit anything under `~/.local/state/claude-sandbox/` or `~/.local/bin/csb`; both are denied for the
  Edit tool.
- Chain a `csb` boundary behind another command, or hide it in `bash -c '…'`, `env csb …` or a script.
  The shapes the hook can read ask, without the summary a canonical call gets; the rest it cannot see
  (a script, a command assembled at run time), and only the ordinary model review stands between
  that call and the boundary.

**A refused boundary is the end of it.** If the user declines `create`, `net on` or `apply`, or the
hook denies one, do not:

- have a subagent or teammate run it for you;
- try again by another route or in another form (a wrapper, a script, a different command that does
  the same thing);
- copy **any** patch, not only a harness patch, into the host tree with Edit, Write, `git apply` or
  `patch`;
- run `python3 ~/.claude/hooks/claude_sandbox.py …` directly.

Report what was refused and let the user decide.

## Known gaps

What this sandbox does not cover (the same uid can forge its records, the dialog and the later run
can differ, the Docker binary's trust, and more) is listed in `.claude/hooks/README.md`, under "The
sandbox CLI stage (`csb`)" → **Known gaps**. Read it before leaning on a `PASS`.
