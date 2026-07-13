---
description: Sync documentation from source of truth - scripts reference, environment variables, and API docs. Invokes the doc-updater agent.
---

# Update Documentation

This command invokes the **doc-updater** agent to sync documentation from source-of-truth. The agent does the work directly — it does not call this command back.

Sync documentation from source-of-truth:

1. Read package.json scripts section
   - Generate scripts reference table
   - Include descriptions from comments

2. Read .env.example
   - Extract all environment variables
   - Document purpose and format

3. Generate docs/CONTRIB.md with:
   - Development workflow
   - Available scripts
   - Environment setup
   - Testing procedures

4. Generate docs/RUNBOOK.md with:
   - Deployment procedures
   - Monitoring and alerts
   - Common issues and fixes
   - Rollback procedures

5. Identify obsolete documentation:
   - Find docs not modified in 90+ days
   - List for manual review

6. Show diff summary

Single source of truth: package.json and .env.example

## Related

- Agent: `agents/doc-updater.md`
- Related command: `/update-codemaps`
