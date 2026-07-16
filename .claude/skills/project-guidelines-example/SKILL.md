---
name: project-guidelines-example
description: Example project-specific skill template with architecture overview, code patterns, testing requirements, and deployment workflow
disable-model-invocation: true
user-invocable: false
---

# Project Guidelines Skill (Template)

A copy-me template for a **project-specific** skill. Drop it into a real project
as `.claude/skills/<project>-guidelines/SKILL.md`, then replace every section
below with that project's actual details. Placeholders are written as
`<YourProject>` / `<...>`.

A project skill captures the things a generic reviewer or coding assistant
can't guess: the stack, the directory layout, the house patterns, how tests and
deploys are run, and the non-negotiable rules.

---

## When to Use

Reference this skill when working on the project it describes. It should carry:

- Architecture overview (stack + service topology)
- File structure (where things live)
- Code patterns (the house style, shown as short snippets)
- Testing requirements (how to run tests and what to cover)
- Deployment workflow (how a change reaches production)

---

## Architecture Overview

Replace with your real stack. Example shape:

- **Frontend**: `<framework>` (e.g. Next.js App Router + TypeScript)
- **Backend**: `<framework>` (e.g. FastAPI + Pydantic)
- **Database**: `<db>` (e.g. PostgreSQL)
- **Deployment**: `<target>` (e.g. Cloud Run / Vercel)
- **Testing**: `<tools>` (e.g. Playwright E2E, pytest, React Testing Library)

Sketch the service topology so a newcomer sees how a request flows:

```
Client  ->  <frontend>  ->  <backend API>  ->  <database> / <external APIs>
```

---

## File Structure

Map the directories a contributor needs to find their way around:

```
<project>/
├── frontend/          # UI (framework, components, hooks, lib, types)
├── backend/           # API handlers, models, services, tests
├── deploy/            # Deployment configs
├── docs/              # Documentation
└── scripts/           # Utility scripts
```

---

## Code Patterns

Show the 2-3 patterns most repeated in the codebase so new code matches. Keep
snippets short and idiomatic. Illustrative example — a typed API-response
envelope shared by both ends:

```python
# backend: a uniform result envelope
class ApiResponse(BaseModel, Generic[T]):
    success: bool
    data: T | None = None
    error: str | None = None
```

```typescript
// frontend: same shape, one fetch wrapper that never throws on HTTP errors
async function fetchApi<T>(
  endpoint: string,
  options?: RequestInit,
): Promise<ApiResponse<T>> {
  try {
    const res = await fetch(`/api${endpoint}`, { ...options });
    if (!res.ok) return { success: false, error: `HTTP ${res.status}` };
    return await res.json();
  } catch (e) {
    return { success: false, error: String(e) };
  }
}
```

---

## Testing Requirements

State the commands and the coverage bar so nobody has to guess:

```bash
# backend
<runner> test            # e.g. pytest tests/
<runner> test --cov      # coverage report

# frontend
<pkg> run test           # unit / component
<pkg> run test:e2e       # end-to-end
```

---

## Deployment Workflow

### Pre-Deployment Checklist

- [ ] All tests passing locally
- [ ] Build succeeds (frontend + backend)
- [ ] No hardcoded secrets
- [ ] Environment variables documented
- [ ] Database migrations ready

### Deployment Commands

```bash
# replace with your real deploy commands
<deploy frontend>
<deploy backend>
```

List required environment variables by name only — never commit real values:

```bash
# frontend
<PUBLIC_API_URL>, <PUBLIC_*_KEY>
# backend
<DATABASE_URL>, <PROVIDER_API_KEY>
```

---

## Critical Rules

Spell out the non-negotiables for the project. Common examples:

1. **No emojis** in code, comments, or documentation
2. **Immutability** - never mutate objects or arrays in place
3. **TDD** - write tests before implementation
4. **Coverage floor** - e.g. 80% minimum
5. **Many small files** - 200-400 lines typical, 800 max
6. **No debug logging** (`console.log` / `print`) in production code
7. **Proper error handling** on every I/O boundary
8. **Input validation** at trust boundaries (Pydantic / Zod)

---

## Related Skills

- `backend-patterns/` - API and database patterns
- `frontend-patterns/` - React and Next.js patterns
- `tdd-workflow/` - test-driven development methodology
