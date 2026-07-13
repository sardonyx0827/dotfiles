---
description: Find and safely remove dead code using knip, depcheck, and ts-prune with test verification at each step. Invokes the refactor-cleaner agent.
---

# Refactor Clean

This command invokes the **refactor-cleaner** agent to find and safely remove dead code, verifying tests at each step.

Safely identify and remove dead code with test verification:

1. Run dead code analysis tools:
   - knip: Find unused exports and files
   - depcheck: Find unused dependencies
   - ts-prune: Find unused TypeScript exports

2. Generate comprehensive report in .reports/dead-code-analysis.md

3. Categorize findings by severity:
   - SAFE: Test files, unused utilities
   - CAUTION: API routes, components
   - DANGER: Config files, main entry points

4. Propose safe deletions only

5. Before each deletion:
   - Run full test suite
   - Verify tests pass
   - Apply change
   - Re-run tests
   - Rollback if tests fail

6. Show summary of cleaned items

Never delete code without running tests first!

## Related

- Agent: `agents/refactor-cleaner.md`
