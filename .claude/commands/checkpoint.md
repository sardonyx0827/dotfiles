---
description: Create, verify, or list workflow checkpoints to track progress through multi-phase tasks. Usage: /checkpoint [create|verify|list] [name]
---

# Checkpoint Command

Create or verify a checkpoint in your workflow.

## Usage

`/checkpoint [create|verify|list] [name]`

## Create Checkpoint

When creating a checkpoint:

1. Run `/verify quick` to ensure current state is clean
2. Snapshot the tracked changes without touching the working tree, the index or the branch, and keep the snapshot reachable under `refs/checkpoints/<name>` (one Bash call; `<name>` must be a valid ref component — no spaces, `..`, `~ ^ : ? * [ \`; `git check-ref-format` is the judge):

```bash
git check-ref-format "refs/checkpoints/$CHECKPOINT_NAME" &&
  sha=$(git stash create "checkpoint $CHECKPOINT_NAME") &&
  git update-ref "refs/checkpoints/$CHECKPOINT_NAME" "${sha:-$(git rev-parse HEAD)}" ""
```

If any of the three fails, stop and report it: do not log a checkpoint whose ref was not created.
The trailing `""` makes `git update-ref` refuse when a checkpoint of that name already exists, instead of silently replacing the earlier snapshot. If it does, ask the user whether to overwrite (then run the command without the `""`) or to use another name.

- Dirty tree: `git stash create` builds a stash-style commit object from the tracked changes (staged and unstaged) and prints its SHA; the ref is what keeps garbage collection from pruning it.
- Clean tree: `git stash create` prints nothing, so the ref points at HEAD.
- Untracked files are never included. Needs at least one commit (the snapshot's first parent is HEAD).
- Do not use `git stash` / `git stash push`: it removes the in-progress work from the tree, and the SHA logged below would be that of the unchanged HEAD, not of the work. `git stash create` leaves the working tree, the index and the stash list as they are.

A checkpoint is a local snapshot ref, not a commit on any branch, and therefore not subject to the commit-time review triggers.

3. Log checkpoint to `.claude/checkpoints.log` (the short SHA is that of the ref):

```bash
echo "$(date +%Y-%m-%d-%H:%M) | $CHECKPOINT_NAME | $(git rev-parse --short "refs/checkpoints/$CHECKPOINT_NAME")" >> .claude/checkpoints.log
```

4. Report checkpoint created

## Verify Checkpoint

When verifying against a checkpoint:

1. Read checkpoint from log, and resolve `refs/checkpoints/<name>` (a log line whose ref is gone was cleared or never created: report it, do not guess)
2. Compare current state to checkpoint against the ref (tracked files only; `git status --short` shows untracked ones):
   - Files added, modified and deleted since checkpoint: `git diff --name-status --no-renames --diff-filter=AMD refs/checkpoints/<name>` (`--no-renames` so a moved file is counted as one deletion and one addition rather than dropped from the list)
   - Build status now (the checkpoint was only created after `/verify quick` passed)
   - Test and coverage deltas only where both runs were measured; otherwise report N/A

3. Report:

```
CHECKPOINT COMPARISON: $NAME
============================
Files changed: X
Tests: +Y passed / -Z failed
Coverage: +X% / -Y%
Build: [PASS/FAIL]
```

## List Checkpoints

Show all checkpoints with:

- Name
- Timestamp
- Git SHA (that of `refs/checkpoints/<name>`)
- Status: `current` when `git diff --quiet refs/checkpoints/<name>` finds no change, otherwise `changed` (the snapshot is a stash-style commit that need not be an ancestor of HEAD, so ahead / behind is not defined)

## Workflow

Typical checkpoint flow:

```
[Start] --> /checkpoint create "feature-start"
   |
[Implement] --> /checkpoint create "core-done"
   |
[Test] --> /checkpoint verify "core-done"
   |
[Refactor] --> /checkpoint create "refactor-done"
   |
[PR] --> /checkpoint verify "feature-start"
```

## Arguments

$ARGUMENTS:

- `create <name>` - Create named checkpoint
- `verify <name>` - Verify against named checkpoint
- `list` - Show all checkpoints
- `clear` - Remove old checkpoints (keeps last 5): list the checkpoints that would go and confirm with the user first (the snapshots cannot be recovered once their refs are deleted), then drop their log lines and delete each removed checkpoint's ref with `git update-ref -d "refs/checkpoints/<name>"` (not a ref whose name a kept log line still uses)
