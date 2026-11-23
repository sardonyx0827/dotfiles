---
description: "Usage: /sandbox [task | status] - Run disposable verification in the csb container sandbox: create, work with exec, export a patch, verify, review, attest, and apply after the user approves. Falls back to a git worktree when Docker is not usable."
---

# Sandbox Command

Run `$ARGUMENTS` (or the current task) in a throwaway container instead of on the host.

## Instructions

Follow the **docker-patterns** skill's `references/sandbox.md` — it is the single source of truth for
the flow (create → exec → export → verify → review → attest → apply), for which `csb` subcommands
ask for confirmation, for the `net on` window, for exit codes, and for what is unsupported. Do not
restate or re-derive those here.

First check that Docker is usable: run `docker version --format '{{.Server.Version}}'` through the
Bash tool with `timeout` set to 15000 ms. If it times out or exits non-zero, or `csb` is not installed
(`~/.local/bin/csb` is missing), say so; do not try to install or start Docker. For ordinary
verification, such as testing a change you wrote yourself, use a git worktree as before. A worktree
shares the host's user, network and secrets, so if the point is to run unreviewed code or to install
dependencies, ask the user before switching to it.

Five rules decide whether the result can be trusted or applied:

- **Review the patch, not the sandbox.** `apply` needs a passing `verify` for the exact patch sha and
  the reviews that `export` names (`code-reviewer`, `codex` and `advisor`, plus `security-reviewer`
  when `export` lists it). Record each with `csb attest`, only for a review that ran and with the
  result it returned. If `advisor` or `codex` cannot run, do not go on to `apply`: tell the user.
- **Stop at every boundary.** `csb create`, `net on` and `apply` ask the user; wait for the answer.
  If one is declined or denied, stop and report. Do not retry it in another form, have a subagent run
  it, copy any patch into the host tree with Edit, Write, `git apply` or `patch`, run
  `claude_sandbox.py` directly, or switch to a worktree or bare `docker` without asking the user.
- **A harness patch is applied by the user, never by you.** `csb apply` always refuses a patch that
  touches `.claude/`, `.codex/`, `.gemini/`, `CLAUDE.md` and the like; show the manual steps it
  prints and stop.
- **`csb image build --generic` is the user's to run**, in their own terminal. The hook denies it to
  an agent, so do not wait for an answer. If `create` says the generic image is missing, ask the user
  to build it.
- **Write every `csb` call in canonical form**: one simple command, payloads in single quotes. A
  call in another form gets no static allow and may reach only the ordinary model review.

## Arguments

`$ARGUMENTS` selects what to do:

| Value         | Action                                                                          |
| ------------- | ------------------------------------------------------------------------------- |
| `status`      | Run `csb status` and report each sandbox's container, network and patch state   |
| anything else | The task to verify in a sandbox (default: the work in the current conversation) |
