---
description: Review SQL, migrations, and schema changes for correctness, performance, and security. Invokes the database-reviewer agent.
---

# Database Review

This command invokes the **database-reviewer** agent for PostgreSQL-focused review of queries, migrations, and schema design (Supabase best practices).

## What This Command Does

1. **Identify DB Changes**: Find modified `.sql` files, migrations, and ORM schema changes via `git diff`
2. **Query Review**: Check for sequential scans, missing indexes, N+1 access, and unbounded result sets
3. **Schema Review**: Validate types, constraints, foreign keys, and normalization
4. **Security Review**: Parameterized queries only (no string-built SQL), Row Level Security, least-privilege grants
5. **Migration Safety**: Flag non-concurrent index builds, blocking locks, and irreversible/destructive steps
6. **Generate Report**: Categorize issues by severity (CRITICAL / HIGH / MEDIUM)

## When to Use

Use `/db-review` when:

- Writing or modifying SQL queries or migrations
- Designing or altering database schemas
- Troubleshooting slow queries or lock contention
- Implementing Row Level Security or connection pooling
- Before committing database changes

## Automated Checks

```bash
# Show pending schema/migration changes
git diff --name-only | grep -E '\.(sql)$|migrations/'

# Inspect a query plan (run against a dev database only)
# EXPLAIN (ANALYZE, BUFFERS) <query>;
```

## Approval Criteria

| Status     | Condition                               |
| ---------- | --------------------------------------- |
| ✅ Approve | No CRITICAL or HIGH issues              |
| ⚠️ Warning | MEDIUM issues only (merge with caution) |
| ❌ Block   | CRITICAL or HIGH issues found           |

## Related

- Agent: `agents/database-reviewer.md`
- Skills: `skills/postgres-patterns/`, `skills/clickhouse-io/`
