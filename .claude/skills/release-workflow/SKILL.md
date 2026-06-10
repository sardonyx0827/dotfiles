---
name: release-workflow
description: >
  End-to-end release process: semver level decisions from Conventional Commits,
  changelog generation, annotated git tags, GitHub Releases via gh CLI, version
  bump locations, hotfix branching, and CI automation shape. Use this skill
  whenever you are cutting a release, bumping a version, generating or updating
  a changelog, creating a git tag or GitHub Release, deciding which semver level
  to apply (major/minor/patch), handling a hotfix release, or working through a
  release checklist. If the user asks "what version should this be?", "how do I
  tag this?", "create a release", or "we need a hotfix" — load this skill first.
---

# Release Workflow

A repeatable, auditable release process built on Conventional Commits, annotated
tags, and the `gh` CLI. Each section explains the command and why it matters.

## 1. Semver Decision from Conventional Commits

Survey every commit since the last tag before picking a version level:

```bash
git log $(git describe --tags --abbrev=0)..HEAD --oneline
```

Apply the highest-ranked change type found:

| Commit signal                                               | Semver bump |
| ----------------------------------------------------------- | ----------- |
| `fix:`                                                      | patch       |
| `feat:`                                                     | minor       |
| `BREAKING CHANGE:` footer or `!` after type (e.g. `feat!:`) | major       |

Precedence is strict: a single `feat!:` overrides all `fix:` commits in the
same batch. When there are no conventional commits at all, default to patch and
document why in the tag message.

❌ WRONG — picking minor because "it feels minor":

```
feat: add dark mode toggle
fix: correct button alignment
# Released as v1.2.1 (patch) — "it's just a toggle"
```

✅ CORRECT — `feat:` mandates minor regardless of perceived size:

```
feat: add dark mode toggle
fix: correct button alignment
# Released as v1.3.0 (minor)
```

## 2. Pre-Release Verification

Never tag a commit you have not verified. Run all of the following before
creating the tag:

```bash
# 1. Working tree must be clean
git status --short
# Expect: no output. Uncommitted changes contaminate the release commit.

# 2. Tests green
# (adjust to the project's actual test command)
npm test        # Node
go test ./...   # Go
pytest          # Python

# 3. Build succeeds
npm run build   # or equivalent

# 4. Changelog drafted (see Section 3)
```

If any step fails, fix it, commit with the appropriate type, and re-survey
commits (Section 1) — the new commit may change the semver level.

## 3. Changelog Generation

### Option A: git-cliff (preferred when available)

```bash
git cliff --tag v1.4.0 --output CHANGELOG.md
```

git-cliff reads `cliff.toml` and groups commits by type automatically. Commit
`CHANGELOG.md` as part of the release commit (`chore: release v1.4.0`).

### Option B: conventional-changelog CLI

```bash
npx conventional-changelog-cli -p angular -i CHANGELOG.md -s
```

### Option C: Plain git log (no tooling required)

```bash
git log $(git describe --tags --abbrev=0)..HEAD \
  --pretty=format:'%s' \
  | sort \
  | awk -F': ' '
    /^feat/   { print "### Added\n- " $2 }
    /^fix/    { print "### Fixed\n- " $2 }
    /^chore/  { print "### Changed\n- " $2 }
  '
```

Structure the output using Keep a Changelog conventions:

```markdown
## [1.4.0] - 2026-06-10

### Added

- Dark mode toggle (#42)

### Changed

- Upgrade dependency foo to v3

### Fixed

- Correct button alignment on mobile

### Removed

- Drop legacy IE11 polyfill
```

Only include sections that have entries. The `Removed` section matters most for
communicating breaking changes to consumers.

## 4. Tagging: Annotated Tags Only

```bash
git tag -a v1.4.0 -m "chore: release v1.4.0

<one-paragraph summary of what changed>"
```

Use annotated tags (`-a`), never lightweight tags. Annotated tags:

- Carry author, date, and message metadata
- Are the target of `git describe` (used in CI, version inference)
- Are pushed separately from commits and require an explicit `--tags` push
- Appear as proper releases on GitHub

❌ WRONG — lightweight tag:

```bash
git tag v1.4.0   # No message, not found by git describe correctly
```

✅ CORRECT — annotated tag:

```bash
git tag -a v1.4.0 -m "chore: release v1.4.0"
git push origin v1.4.0
```

Push the tag explicitly; `git push` alone does not push tags.

## 5. GitHub Release with gh CLI

### Basic release (notes from file)

```bash
gh release create v1.4.0 \
  --title "v1.4.0 — Dark mode & alignment fixes" \
  --notes-file CHANGELOG_v1.4.0.md
```

### Auto-generated notes

```bash
gh release create v1.4.0 \
  --title "v1.4.0" \
  --generate-notes
```

`--generate-notes` uses GitHub's built-in diff-based notes. Useful as a
fallback when a changelog file is not ready.

### Draft → publish flow (recommended for major releases)

```bash
# Create as draft first — review in the GitHub UI before publishing
gh release create v1.4.0 \
  --title "v1.4.0" \
  --notes-file CHANGELOG_v1.4.0.md \
  --draft

# After review, publish
gh release edit v1.4.0 --draft=false
```

### Release candidates and prereleases

```bash
gh release create v2.0.0-rc.1 \
  --title "v2.0.0-rc.1 (Release Candidate)" \
  --notes-file CHANGELOG_rc1.md \
  --prerelease
```

### Attach build artifacts

```bash
gh release create v1.4.0 \
  --title "v1.4.0" \
  --notes-file CHANGELOG_v1.4.0.md \
  dist/app-linux-amd64 dist/app-darwin-arm64
```

## 6. Version Bump Locations

Keep version in one canonical source. Never let package.json and a git tag
drift apart.

| Ecosystem | Canonical file                       | Command                                  |
| --------- | ------------------------------------ | ---------------------------------------- |
| Node.js   | `package.json` `.version`            | `npm version 1.4.0 --no-git-tag-version` |
| Go        | git tag (no file)                    | tag directly; `go get module@v1.4.0`     |
| Python    | `pyproject.toml` `[project].version` | edit manually or `poetry version 1.4.0`  |

Commit the version bump before tagging:

```bash
npm version 1.4.0 --no-git-tag-version   # edits package.json only
git add package.json package-lock.json
git commit -m "chore: bump version to 1.4.0"
git tag -a v1.4.0 -m "chore: release v1.4.0"
```

`--no-git-tag-version` prevents `npm version` from creating its own tag; you
create the annotated tag yourself in the next step.

## 7. Hotfix Flow

A hotfix targets a specific release, not the current state of `main`. Branch
from the release tag so you pick up exactly what users are running:

```bash
# Branch from the release tag, not from main
git checkout -b hotfix/v1.3.1 v1.3.0

# Make the minimal fix; commit with fix: type
git commit -m "fix: prevent nil pointer dereference in auth handler"

# Tag and release
git tag -a v1.3.1 -m "chore: release v1.3.1 (hotfix)"
git push origin v1.3.1
gh release create v1.3.1 --title "v1.3.1 (hotfix)" --generate-notes

# Cherry-pick or merge back to main so the fix is not lost
git checkout main
git cherry-pick <hotfix-commit-sha>
git push origin main
```

❌ WRONG — hotfix branch from `main`:
The fix lands in the next major release but not in the patch for the
version users are actually running.

✅ CORRECT — branch from the tag, cherry-pick back to `main`.

## 8. Automating in CI

Tag pushes are the natural trigger for a release pipeline. A minimal
GitHub Actions shape (full workflow patterns live in the `github-actions-ci`
skill; this section covers only the release-specific structure):

```yaml
on:
  push:
    tags:
      - "v[0-9]+.[0-9]+.[0-9]+" # semver tags only

jobs:
  release:
    runs-on: ubuntu-latest
    permissions:
      contents: write # required for gh release create
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0 # full history needed for git-cliff / git describe

      - name: Run tests
        run: npm test # never skip in CI

      - name: Build artifacts
        run: npm run build

      - name: Generate changelog
        run: git cliff --tag ${{ github.ref_name }} --output notes.md

      - name: Create GitHub Release
        run: |
          gh release create "${{ github.ref_name }}" \
            --title "${{ github.ref_name }}" \
            --notes-file notes.md \
            dist/*
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

Keep `fetch-depth: 0` — shallow clones break `git describe` and changelog
tools that walk tag history.

## Release Checklist

Before every release:

- [ ] `git log $(git describe --tags --abbrev=0)..HEAD --oneline` surveyed
- [ ] Semver level chosen and justified (patch / minor / major)
- [ ] Working tree clean (`git status --short` returns nothing)
- [ ] Full test suite green
- [ ] Build succeeds
- [ ] `CHANGELOG.md` updated and committed
- [ ] Version bumped in canonical file (package.json / pyproject.toml) and committed
- [ ] Annotated tag created with `git tag -a vX.Y.Z -m "..."`
- [ ] Tag pushed with `git push origin vX.Y.Z`
- [ ] GitHub Release created via `gh release create`
- [ ] For hotfixes: fix cherry-picked or merged back to `main`
- [ ] For prereleases: `--prerelease` flag set on `gh release create`
