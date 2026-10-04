---
description: Scan the codebase structure and regenerate token-lean architecture codemaps under docs/CODEMAPS/ (index, backend, frontend, database). Invokes the doc-updater agent.
---

# Update Codemaps

This command invokes the **doc-updater** agent to scan the codebase and regenerate token-lean architecture codemaps.

Analyze the codebase structure and update architecture documentation:

1. Scan all source files for imports, exports, and dependencies
2. Generate token-lean codemaps under docs/CODEMAPS/ — at minimum the files below; agents/doc-updater.md
   defines the full layout and format:
   - docs/CODEMAPS/INDEX.md - Overall architecture
   - docs/CODEMAPS/backend.md - Backend structure
   - docs/CODEMAPS/frontend.md - Frontend structure
   - docs/CODEMAPS/database.md - Data models and schemas

3. Calculate diff percentage from previous version
4. If changes > 30%, request user approval before updating
5. Add freshness timestamp to each codemap
6. Save reports to .reports/codemap-diff.txt

Use TypeScript/Node.js for analysis. Focus on high-level structure, not implementation details.

## Related

- Agent: `agents/doc-updater.md`
- Related command: `/update-docs`
