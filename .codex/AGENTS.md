# AGENTS.md

## 0. 目的

このプロジェクトで Codex が作業する際のポリシーを明示する

## 1. 言語ポリシー（必須）

- すべての対話・出力は **日本語** で行うこと
- **Git のコミットメッセージは英語**で記述すること（Conventional Commits に従う）

## 2. ブラウザ操作（必須）

- fetch/curl が必要な場合は理由を説明してから実行すること

## 3. Git ワークフロー

### コミットメッセージのフォーマット

```
<type>: <description>

<optional body>
```

- Written in English, following Conventional Commits
- Summary ~50 characters, add body if needed
- Types: feat, fix, refactor, docs, test, chore, perf, ci

### Command Triggers

#### push を要求された場合（例: "push して", "プッシュして", "push this"）

1. Review changes (`git status` / `git diff`)
2. Stage files (`git add`) — skip if already staged
3. Commit following the commit message format above
4. Push to remote (propose PR creation if direct push to default branch is inappropriate)

#### commit を要求された場合（例: "commit して", "コミットして", "commit this"）

1. Review changes (`git status` / `git diff`)
2. Stage files (`git add`) — skip if already staged
3. Commit following the commit message format above

#### PR 作成を要求された場合（例: "pr作成して", "PR作って", "create a PR"）

1. Review current changes and branch structure
2. Create a new branch from the current branch (naming: `fix/`, `feat/`, `style/` prefix)
3. Commit following the commit message format above
4. Push the new branch to remote
5. Create a Pull Request against the original branch (follow PR quality standards below)
6. Switch back to the original branch
7. Suggest deleting the working branch after merge

### Pull Request クオリティ基準

1. Analyze full commit history (not just latest commit)
2. Use `git diff [base-branch]...HEAD` to see all changes
3. Draft comprehensive PR summary in Japanese
4. Include test plan with TODOs
5. Push with `-u` flag if new branch
6. If direct push to default branch is inappropriate, propose PR creation

### 機能実装ワークフロー

1. **Plan First**
   - Create implementation plan
   - Identify dependencies and risks
   - Break down into phases

2. **TDD Approach**
   - Write tests first (RED)
   - Implement to pass tests (GREEN)
   - Refactor (IMPROVE)
   - Verify 80%+ coverage

3. **Code Review**
   - Review code after writing
   - Address CRITICAL and HIGH issues
   - Fix MEDIUM issues when possible

4. **Commit & Push**
   - Detailed commit messages
   - Follow conventional commits format

## 4. セーフティガード

- ファイル編集・依存追加・外部通信は、プロジェクトの既定ルールに従うこと
- 危険と判断した操作は実行前にユーザーに確認を求めること
- ブラウザ自動化の対象サイトは最小限に限定し、個人情報や秘密情報を扱わない

## 5. コーディングスタイル

### Immutability (CRITICAL)

ALWAYS create new objects, NEVER mutate:

```javascript
// WRONG: Mutation
function updateUser(user, name) {
  user.name = name; // MUTATION!
  return user;
}

// CORRECT: Immutability
function updateUser(user, name) {
  return {
    ...user,
    name,
  };
}
```

### File Organization

MANY SMALL FILES > FEW LARGE FILES:

- High cohesion, low coupling
- 200-400 lines typical, 800 max
- Extract utilities from large components
- Organize by feature/domain, not by type

### Error Handling

ALWAYS handle errors comprehensively:

```typescript
try {
  const result = await riskyOperation();
  return result;
} catch (error) {
  console.error("Operation failed:", error);
  throw new Error("Detailed user-friendly message");
}
```

### Input Validation

ALWAYS validate user input:

```typescript
import { z } from "zod";

const schema = z.object({
  email: z.string().email(),
  age: z.number().int().min(0).max(150),
});

const validated = schema.parse(input);
```

### Code Quality Checklist

Before marking work complete:

- [ ] Code is readable and well-named
- [ ] Functions are small (<50 lines)
- [ ] Files are focused (<800 lines)
- [ ] No deep nesting (>4 levels)
- [ ] Proper error handling
- [ ] No console.log statements
- [ ] No hardcoded values
- [ ] No mutation (immutable patterns used)

## 6. セキュリティ

### Mandatory Security Checks

Before ANY commit:

- [ ] No hardcoded secrets (API keys, passwords, tokens)
- [ ] All user inputs validated
- [ ] SQL injection prevention (parameterized queries)
- [ ] XSS prevention (sanitized HTML)
- [ ] CSRF protection enabled
- [ ] Authentication/authorization verified
- [ ] Rate limiting on all endpoints
- [ ] Error messages don't leak sensitive data

### Secret Management

```typescript
// NEVER: Hardcoded secrets
const apiKey = "sk-proj-xxxxx";

// ALWAYS: Environment variables
const apiKey = process.env.OPENAI_API_KEY;

if (!apiKey) {
  throw new Error("OPENAI_API_KEY not configured");
}
```

### Security Response Protocol

If security issue found:

1. STOP immediately
2. セキュリティの根本原因を分析する
3. Fix CRITICAL issues before continuing
4. Rotate any exposed secrets
5. Review entire codebase for similar issues

## 7. テスト要件

### Minimum Test Coverage: 80%

Test Types (ALL required):

1. **Unit Tests** - Individual functions, utilities, components
2. **Integration Tests** - API endpoints, database operations
3. **E2E Tests** - Critical user flows (Playwright)

### Test-Driven Development

MANDATORY workflow:

1. Write test first (RED)
2. Run test - it should FAIL
3. Write minimal implementation (GREEN)
4. Run test - it should PASS
5. Refactor (IMPROVE)
6. Verify coverage (80%+)

### Troubleshooting Test Failures

1. Check test isolation
2. Verify mocks are correct
3. Fix implementation, not tests (unless tests are wrong)

## 8. 共通パターン

### API Response Format

```typescript
interface ApiResponse<T> {
  success: boolean;
  data?: T;
  error?: string;
  meta?: {
    total: number;
    page: number;
    limit: number;
  };
}
```

### Custom Hooks Pattern

```typescript
export function useDebounce<T>(value: T, delay: number): T {
  const [debouncedValue, setDebouncedValue] = useState<T>(value);

  useEffect(() => {
    const handler = setTimeout(() => setDebouncedValue(value), delay);
    return () => clearTimeout(handler);
  }, [value, delay]);

  return debouncedValue;
}
```

### Repository Pattern

```typescript
interface Repository<T> {
  findAll(filters?: Filters): Promise<T[]>;
  findById(id: string): Promise<T | null>;
  create(data: CreateDto): Promise<T>;
  update(id: string, data: UpdateDto): Promise<T>;
  delete(id: string): Promise<void>;
}
```

### Skeleton Projects

When implementing new functionality:

1. Search for battle-tested skeleton projects
2. Evaluate options (security, extensibility, relevance)
3. Clone best match as foundation
4. Iterate within proven structure
