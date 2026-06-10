---
name: migration-playbook
description: >
  Structured playbook for safely executing migrations: upgrading a major dependency version,
  framework migrations (e.g. React 17→18, Next.js 12→14, Vue 2→3), API deprecations,
  replacing one library with another, large-scale refactors touching many call sites, and
  database schema changes. Use this skill whenever the word "migrate", "upgrade", "deprecate",
  or "replace" applies to something with more than a handful of affected sites — even if it
  looks straightforward. Migrations that skip the inventory step consistently produce
  incidents from the sites nobody knew existed.
---

# Migration Playbook

A phased workflow for executing migrations safely. The core discipline: **inventory before
strategy, safety net before execution, reviewable batches before merge.** Migrations
fail not because the happy path is wrong, but because the affected surface was larger than
expected, or the rollback story was never written.

---

## Phase 0: Inventory

Enumerate every affected call site BEFORE changing a single line of production code.

```bash
# Count and locate all usages of the thing being replaced
grep -rn "oldImport\|OldAPI\|deprecated_fn" src/ --include="*.ts" | wc -l
grep -rn "oldImport\|OldAPI\|deprecated_fn" src/ --include="*.ts"

# For syntactic patterns, ast-grep gives structural matches
ast-grep --pattern 'require("old-package")' --lang js

# Categorize: how many usage *patterns* exist, not just raw sites?
# e.g. "called with callback" vs "called with async/await" are two patterns
```

Read the official migration guide and CHANGELOG of the dependency **end-to-end** before
writing code. Note every breaking change, removed API, and renamed option. Create a
two-column table: old construct → new construct.

Estimate blast radius:

| Dimension       | Low (<20 sites) | Medium (20-100) | High (>100)   |
| --------------- | --------------- | --------------- | ------------- |
| Strategy        | Big bang        | Incremental     | Codemod first |
| Risk            | Low             | Medium          | High          |
| Branch lifetime | Hours           | Days            | Weeks         |

Why: the sites you did not find are the ones that cause the production incident. A 15-minute
grep pass is cheap insurance against a 3 AM rollback.

---

## Phase 1: Safety Net

Ensure tests cover the behavior being migrated before the first change.

1. Run the full test suite — **it must be green before you start.** A red suite means you
   cannot tell whether a new failure was caused by your migration.
2. Identify which behaviors have no automated coverage. Add **characterization tests** for
   those paths: record what the current code actually does, not what you wish it did. These
   tests exist solely to catch regressions; they can be deleted after migration if coverage
   from proper tests is added.
3. Create a dedicated branch. Never migrate on a long-lived shared branch where others are
   committing.
4. Record the baseline: output of the full suite, any manual smoke-test results, any
   performance benchmarks that could regress.

Why: a migration without a safety net is a rewrite without tests. You will not know what you
broke until a user tells you.

---

## Phase 2: Strategy Choice

Choose one strategy and commit to it. Mixed strategies within the same migration create
dead code and reviewer confusion.

### A. Big Bang

Replace all sites in a single commit (or one tightly scoped PR).

Use when: blast radius is low (fewer than ~20 sites), the API change is atomic, and no
runtime compatibility window is needed.

### B. Incremental with Compatibility Layer

Introduce an adapter that satisfies both the old and new interface. Migrate call sites
batch by batch. Remove the adapter as the last step.

```typescript
// compat/legacyFoo.ts — temporary adapter, delete after migration complete
export function legacyFoo(args: OldArgs): OldResult {
  return newFoo(transformArgs(args));
}
```

Use when: blast radius is medium-to-high, multiple teams share the codebase, or the
migration will span multiple PRs. The key constraint: **the adapter must be tracked as
a TODO item with an explicit removal milestone.** Compatibility layers that outlive
the migration become permanent debt.

### C. Expand → Migrate → Contract (for DB schemas and public APIs)

1. **Expand**: add the new column/endpoint/field alongside the old one. Dual-write to both.
2. **Migrate**: switch readers to the new path. Verify in production. Migrate existing data.
3. **Contract**: remove the old column/endpoint/field in a separate release.

Never combine the "start using new schema" and "drop old schema" steps in the same
release. The gap between them is your rollback window.

Use when: the change affects a database schema, a public API consumed by external clients,
or any surface where you cannot coordinate all consumers simultaneously.

### D. Codemod

Write a codemod (jscodeshift, ast-grep, custom script) to perform the mechanical
transformation. Review the generated diff as you would any PR.

Use when: sites > ~20 AND the transformation is purely syntactic (rename, argument
reorder, import path change). Do not use codemods for semantic changes that require
understanding context — mechanical transforms applied to ambiguous sites introduce
subtle bugs that grep cannot find.

---

## Phase 3: Execute in Reviewable Batches

One batch = one commit that leaves the full test suite green.

- Never commit a half-migrated state. The tree must be buildable and testable at every
  commit — a team member should be able to check out any commit and run `ci` without
  fixing anything first.
- Keep batches small enough to review in under 30 minutes. If a commit diff is too large
  to understand, split it.
- When using a compatibility layer, track its removal explicitly:

```markdown
TODO(migration): remove legacyFoo adapter after all sites migrated
Tracking: <issue/PR link>
```

- Log each batch as it completes: which file groups were migrated, which patterns remain.
  This log is the rollback map if something goes wrong mid-migration.

For dependency major-version bumps specifically:

- Upgrade one major version at a time (v2→v3, then v3→v4; never v2→v4 in one step).
- Run with deprecation warnings promoted to errors first: catch the easy problems before
  the breaking ones.
- Lock transitive dependencies explicitly after the upgrade to prevent future drift from
  silently breaking the same paths.

---

## Phase 4: Verify and Clean Up

Do not close the migration until every item in this phase is complete.

1. Run the full test suite. Every test that was green at Phase 1 must still be green.
2. Manually smoke-test the highest-risk paths (auth flows, data-write paths, public API
   endpoints, anything with money or user data).
3. Grep for stragglers:
   ```bash
   # Old import paths still present?
   grep -rn "old-package\|OldClass\|deprecated_fn" src/
   # Compatibility layer still referenced?
   grep -rn "legacyFoo\|compat/" src/
   ```
4. Remove the compatibility layer, the adapter module, and any dead configuration that
   existed only to support the old behavior. An incomplete clean-up leaves a trap for the
   next developer.
5. Update documentation: README, API docs, internal runbooks, CHANGELOG. If a migration
   guide existed for this dependency, add a note in your project's docs explaining which
   version the project is now on and when the migration was completed.
6. Delete the characterization tests added in Phase 1 if they are fully superseded by
   proper test coverage.

---

## Rollback Planning

Each phase must be individually revertable. Before starting Phase 3, answer:

- **Phase 0-1 rollback**: trivially, delete the branch.
- **Phase 3 rollback**: which commit(s) does `git revert` need to touch? Name them.
- **Phase 4 (compat layer removal) rollback**: restoring the adapter should require at
  most one commit that passes CI.

For runtime switchover (feature flag pattern):

```typescript
// Toggle between old and new implementation without redeploy
const result = flags.isEnabled("new-foo-impl") ? newFoo(args) : legacyFoo(args);
```

For database migrations: the "contract" step (dropping the old column) must never be in
the same release as the code that begins using the new column. The deploy gap is the
rollback window. If the new code has a bug, you need to be able to roll back the application
without also needing to restore data.

---

## Anti-Patterns

- **Migrating without an inventory** — starting by editing the first file you find and
  discovering 40 more sites mid-PR.
- **Red CI at start** — beginning a migration when the suite is already failing; you
  cannot distinguish new regressions from old ones.
- **Compatibility layer without a removal date** — an adapter with no tracked issue becomes
  permanent. Name the milestone when you create it.
- **Combining expand and contract in one release** — the most common cause of irreversible
  DB migrations. Always separate them.
- **Skipping the CHANGELOG** — assuming you know what broke from the major-version number.
  Read the actual breaking changes list; surprises are always in the details.
- **Big-bang on a large blast radius** — a 400-site replacement in a single commit is
  unreviable and unrollable. Use incremental or codemod instead.
- **Jumping multiple major versions** — upgrading v1→v4 in one step means debugging
  the interaction of three major breaking-change sets simultaneously.
- **Grepping only source, not tests and scripts** — old API calls in test helpers and
  build scripts fail in CI, not in production, but they still block the migration.

---

## Quick-Reference Checklist

### Before starting

- [ ] Inventory complete: all affected sites counted and categorized
- [ ] Official migration guide read end-to-end
- [ ] Blast radius estimated → strategy chosen
- [ ] Full test suite green on current branch

### During execution

- [ ] Compatibility layer (if used) has a tracked removal issue
- [ ] Every commit leaves the tree green and buildable
- [ ] Batches are small enough to review in <30 min
- [ ] Rollback path documented for each phase

### Before closing

- [ ] Full suite green
- [ ] Smoke-tested high-risk paths manually
- [ ] Grep confirms zero old import paths / deprecated calls remain
- [ ] Compatibility layer and dead config removed
- [ ] Docs and CHANGELOG updated
- [ ] Characterization tests removed or replaced with proper coverage
