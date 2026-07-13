---
description: Incrementally fix TypeScript and build errors. Invokes the build-error-resolver agent to run the build, group errors by file, apply minimal fixes, and re-verify after each change.
---

# Build and Fix

This command invokes the **build-error-resolver** agent to incrementally fix TypeScript and build errors with minimal, surgical changes.

Incrementally fix TypeScript and build errors:

1. Run build: npm run build or pnpm build

2. Parse error output:
   - Group by file
   - Sort by severity

3. For each error:
   - Show error context (5 lines before/after)
   - Explain the issue
   - Propose fix
   - Apply fix
   - Re-run build
   - Verify error resolved

4. Stop if:
   - Fix introduces new errors
   - Same error persists after 3 attempts
   - User requests pause

5. Show summary:
   - Errors fixed
   - Errors remaining
   - New errors introduced

Fix one error at a time for safety!

## Related

- Agent: `agents/build-error-resolver.md`
- Related commands: `/verify`, `/tdd`
