# Git Workflow

## Commit Message Format

```
<type>: <description>

<optional body>
```

- Written in English, following Conventional Commits
- Summary ~50 characters, add body if needed
- Types: feat, fix, refactor, docs, test, chore, perf, ci
- Note: Attribution disabled globally via ~/.claude/settings.json.

## Command Triggers

### When the user requests a push (e.g. "push して", "プッシュして", "push this")

1. Review changes (`git status` / `git diff`)
2. Stage files (`git add`) — skip if already staged
3. Commit following the commit message format above
4. Push to remote (propose PR creation if direct push to default branch is inappropriate)

### When the user requests a commit (e.g. "commit して", "コミットして", "commit this")

1. Review changes (`git status` / `git diff`)
2. Stage files (`git add`) — skip if already staged
3. Commit following the commit message format above

### When the user requests PR creation (e.g. "pr作成して", "PR作って", "create a PR")

1. Review current changes and branch structure
2. Create a new branch from the current branch (naming: `fix/`, `feat/`, `style/` prefix)
3. Commit following the commit message format above
4. Push the new branch to remote
5. Create a Pull Request against the original branch (follow PR quality standards below)
6. Switch back to the original branch
7. Suggest deleting the working branch after merge

## Pull Request Quality Standards

1. Analyze full commit history (not just latest commit)
2. Use `git diff [base-branch]...HEAD` to see all changes
3. Draft comprehensive PR summary in Japanese
4. Include test plan with TODOs
5. Push with `-u` flag if new branch
6. If direct push to default branch is inappropriate, propose PR creation

## Feature Implementation Workflow

1. **Plan First**
   - Use **planner** agent to create implementation plan
   - Identify dependencies and risks
   - Break down into phases

2. **TDD Approach**
   - Use **tdd-guide** agent
   - Write tests first (RED)
   - Implement to pass tests (GREEN)
   - Refactor (IMPROVE)
   - Verify 80%+ coverage

3. **Code Review**
   - Use **code-reviewer** agent immediately after writing code
   - Address CRITICAL and HIGH issues
   - Fix MEDIUM issues when possible

4. **Commit & Push**
   - Detailed commit messages
   - Follow conventional commits format
