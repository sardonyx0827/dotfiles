---
name: typescript-testing
description: >
  TypeScript and JavaScript testing patterns using Vitest (primary) and Jest.
  Activate this skill whenever you are writing or fixing tests in a TypeScript or
  JavaScript project, touching *.test.ts / *.spec.ts / *.test.tsx files, configuring
  vitest.config.ts or jest.config.ts, asking about mocking strategies (vi.fn, vi.mock,
  MSW), debugging flaky async tests, or wiring up coverage thresholds. Also activates
  when test output shows "floating promise", "timeout", or "cannot find module" in a
  test context. Use this skill proactively — do not wait for the user to ask.
---

# TypeScript Testing Patterns

Reliable, maintainable tests in TypeScript using Vitest as the primary runner.
Jest equivalents are noted where syntax differs.

For TDD methodology and the coverage policy, see the **tdd-workflow** skill.

---

## 1. Test Structure

Name every `describe` and `it` block so the full path reads as a plain sentence.
A reader should understand what is being tested and what is expected without
opening the source file.

```ts
// ❌ WRONG: cryptic names, no context
describe("utils", () => {
  it("test1", () => { ... })
  it("works", () => { ... })
})

// ✅ CORRECT: subject → scenario → expectation
describe("formatCurrency", () => {
  it("returns a USD string when given a positive amount", () => {
    // Arrange
    const amount = 1234.5

    // Act
    const result = formatCurrency(amount, "USD")

    // Assert
    expect(result).toBe("$1,234.50")
  })

  it("returns '—' when amount is null", () => {
    expect(formatCurrency(null, "USD")).toBe("—")
  })
})
```

**Why Arrange-Act-Assert matters**: blank lines between phases make failures
obvious and diffs readable. One behavior per test — when a test has two
`expect` calls that test different behaviors, split it.

---

## 2. Table-Driven Tests with `test.each`

Mirror the Go table-test philosophy: enumerate cases in a data structure,
not in repeated `it` blocks. This scales from 2 cases to 20 without duplication.

```ts
// ✅ CORRECT: test.each with tagged template literals (Vitest / Jest identical)
describe("clamp", () => {
  test.each([
    ["below min", -5, 0, 100, 0],
    ["above max", 200, 0, 100, 100],
    ["within range", 42, 0, 100, 42],
    ["at min boundary", 0, 0, 100, 0],
    ["at max boundary", 100, 0, 100, 100],
  ])("%s", (_label, value, min, max, expected) => {
    expect(clamp(value, min, max)).toBe(expected);
  });
});

// ❌ WRONG: duplicated it-blocks for each case
it("returns min when below", () => {
  expect(clamp(-5, 0, 100)).toBe(0);
});
it("returns max when above", () => {
  expect(clamp(200, 0, 100)).toBe(100);
});
// ...five more nearly-identical blocks
```

For error cases, add a boolean `shouldThrow` column and branch on it inside
the single test body — no need for a separate `it` block per error path.

---

## 3. Mocking Strategy Hierarchy

Prefer the least-invasive option that makes the test readable and stable.

```
1. Dependency injection — pass a fake object into the constructor / function
2. vi.fn() passed in    — inline stub, no module patching
3. vi.spyOn             — intercept one method on a real object
4. vi.mock (module)     — last resort; only when you cannot inject
```

### Why module mocks are brittle

`vi.mock('../../services/email')` patches the module registry. This causes:

- **Hoisting surprises**: Vitest hoists `vi.mock` calls to the top of the file,
  so references to outer variables often fail silently.
- **Type drift**: The mock's shape diverges from the real module as the source
  evolves; TypeScript will not catch this.
- **Hidden coupling**: Every test file that imports the module is affected,
  making test order and import side-effects matter.

```ts
// ❌ WRONG: brittle module mock
vi.mock("../../lib/mailer", () => ({ sendEmail: vi.fn() }))

// ✅ CORRECT: inject the dependency
interface Mailer { sendEmail(to: string, body: string): Promise<void> }

function createOrderService(mailer: Mailer) { ... }

it("sends a confirmation email after checkout", async () => {
  const mailer: Mailer = { sendEmail: vi.fn().mockResolvedValue(undefined) }
  const svc = createOrderService(mailer)
  await svc.checkout(order)
  expect(mailer.sendEmail).toHaveBeenCalledWith(order.email, expect.any(String))
})
```

### `vi.spyOn` with `mockRestore`

Use `spyOn` when you need to intercept a method on a real object and want the
original restored automatically. Always call `mockRestore` in `afterEach`.

```ts
afterEach(() => vi.restoreAllMocks()); // restores every spyOn in this suite

it("logs a warning on partial failure", () => {
  const spy = vi.spyOn(console, "warn").mockImplementation(() => {});
  processPartialResult(data);
  expect(spy).toHaveBeenCalledOnce();
});
```

---

## 4. Async Testing

Always `await` async assertions. A floating promise returns `undefined`
synchronously — the test passes before the rejection or assertion runs.

```ts
// ❌ WRONG: floating promise — test always passes
it("rejects on invalid id", () => {
  expect(fetchUser("")).rejects.toThrow("ID required");
});

// ✅ CORRECT: awaited
it("rejects on invalid id", async () => {
  await expect(fetchUser("")).rejects.toThrow("ID required");
});

// ✅ CORRECT: resolves
it("returns a user for a valid id", async () => {
  await expect(fetchUser("u_123")).resolves.toMatchObject({ id: "u_123" });
});
```

Never put bare `expect` calls after an `await` that may throw — the assertion
will never run. Use `try/catch` or `rejects` instead.

---

## 5. Fake Timers

Use `vi.useFakeTimers` for debounce, throttle, polling, and retry logic.
Real `setTimeout` in tests introduces sleep and makes CI non-deterministic.

```ts
// ❌ WRONG: real timer — slow and fragile
it("debounces search", async () => {
  triggerSearch("q");
  await new Promise((r) => setTimeout(r, 350)); // 350 ms sleep
  expect(mockFetch).toHaveBeenCalledOnce();
});

// ✅ CORRECT: fake timers — instant and deterministic
describe("useDebounce", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers()); // always restore

  it("delays the callback by the given interval", () => {
    const cb = vi.fn();
    startDebounce(cb, 300);

    vi.advanceTimersByTime(299);
    expect(cb).not.toHaveBeenCalled();

    vi.advanceTimersByTime(1);
    expect(cb).toHaveBeenCalledOnce();
  });
});
```

`vi.runAllTimers()` drains every pending timer; `vi.advanceTimersByTimeAsync`
handles async callbacks (Vitest ≥ 1.2).  
Jest equivalent: `jest.useFakeTimers()` / `jest.advanceTimersByTime()`.

---

## 6. HTTP Boundaries: MSW

Mock at the network level, not at the HTTP client level. Mocking `fetch` or
`axios` directly leaks implementation details and breaks when you switch
clients. MSW intercepts at the `fetch` / `XMLHttpRequest` layer, so your
production code stays untouched.

```ts
// vitest.setup.ts
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";

export const server = setupServer(
  http.get("/api/users/:id", ({ params }) => {
    return HttpResponse.json({ id: params.id, name: "Alice" });
  }),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers()); // prevent handler leakage
afterAll(() => server.close());

// test file
it("displays the user name", async () => {
  const user = await getUser("u_1");
  expect(user.name).toBe("Alice");
});

// Override for an error case in one test
it("throws on 404", async () => {
  server.use(
    http.get("/api/users/:id", () => HttpResponse.json({}, { status: 404 })),
  );
  await expect(getUser("missing")).rejects.toThrow();
});
```

`onUnhandledRequest: "error"` surfaces forgotten mocks immediately instead of
silently returning `undefined`.

---

## 7. Test Isolation

Every test must be able to run in any order and in parallel with any other test.
Shared mutable state is the primary source of flaky tests.

❌ WRONG: declare `const cart = new Cart()` at module scope — tests that
mutate `cart` will pass or fail depending on run order.

✅ CORRECT: declare `let cart: Cart` and assign `cart = new Cart()` inside
`beforeEach`. Each test gets a clean instance regardless of order.

Rules:

- Reset state in `beforeEach`, not `afterEach` (`afterEach` does not run on failure).
- Never export a singleton from a module under test; inject it.
- Set and restore environment variables inside `beforeEach` / `afterEach`.

---

## 8. What NOT to Test

Testing the wrong things inflates coverage numbers while adding no safety net.

| Skip                                                                 | Why                                                       |
| -------------------------------------------------------------------- | --------------------------------------------------------- |
| Private / internal functions                                         | Test through the public API; internals are free to change |
| Third-party library behavior                                         | You don't own it; their own tests cover it                |
| TypeScript type constraints                                          | The compiler enforces these; no runtime test needed       |
| Implementation detail (e.g., which sub-method was called internally) | Ties tests to structure, not behavior                     |
| Trivial getters / setters with no logic                              | Not worth the maintenance cost                            |

```ts
// ❌ WRONG: testing the internal call sequence
it("calls _buildQuery before _executeQuery", () => {
  const buildSpy = vi.spyOn(repo as any, "_buildQuery");
  const execSpy = vi.spyOn(repo as any, "_executeQuery");
  repo.findAll();
  expect(buildSpy).toHaveBeenCalledBefore(execSpy); // fragile
});

// ✅ CORRECT: test the observable behavior
it("returns all matching records", async () => {
  const results = await repo.findAll({ status: "active" });
  expect(results).toHaveLength(3);
  expect(results.every((r) => r.status === "active")).toBe(true);
});
```

---

## 9. Coverage Configuration

Configure coverage in `vitest.config.ts` using the `v8` provider (built into
Node, no extra install). See the **tdd-workflow** skill for the coverage policy.

```ts
// vitest.config.ts
import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    coverage: {
      provider: "v8",
      reporter: ["text", "lcov", "html"],
      exclude: [
        "**/*.d.ts",
        "**/generated/**",
        "**/*.config.ts",
        "**/index.ts", // re-export barrels add noise
      ],
      thresholds: {
        lines: 80,
        functions: 80,
        branches: 75,
        statements: 80,
      },
    },
  },
});
```

Run coverage locally:

```bash
npx vitest run --coverage          # single run
npx vitest --coverage              # watch mode
```

CI: add `--coverage.enabled=true` and fail the pipeline when thresholds are
not met (Vitest exits with code 1 automatically).

Jest equivalent: `jest --coverage` with `coverageThreshold` in `jest.config.ts`.

---

## 10. React Component Testing (brief)

Use `@testing-library/react`. Query priority matters — use the selector that
reflects how a real user finds the element:

```
getByRole > getByLabelText > getByPlaceholderText > getByText > getByTestId
```

`getByTestId` is a last resort; it tests implementation, not accessibility.

Use `userEvent` instead of `fireEvent` — it simulates real browser interactions
(focus, pointer events, keyboard sequence) rather than a single synthetic event.
Keep component tests focused on behavior visible to the user; interact through
`userEvent.type` / `userEvent.click` and assert against what the user sees, not
against internal component state or props.

---

## Checklist

Before marking a test suite complete:

- [ ] Each `it` / `test` name reads as a full sentence describing behavior
- [ ] Arrange-Act-Assert sections separated by blank lines
- [ ] One behavior per test (no multi-assertion "kitchen sink" tests)
- [ ] Repeated cases use `test.each`, not copy-pasted `it` blocks
- [ ] Dependencies are injected; `vi.mock` used only where injection is impossible
- [ ] Every `vi.spyOn` is restored via `vi.restoreAllMocks()` in `afterEach`
- [ ] All async assertions are `await`-ed (`await expect(...).rejects/resolves`)
- [ ] No `setTimeout` or `sleep` — fake timers used for time-dependent logic
- [ ] HTTP calls mocked via MSW, not by patching `fetch` / `axios`
- [ ] `beforeEach` resets all mutable state; no cross-test dependencies
- [ ] Private methods and third-party behavior are not tested directly
- [ ] Coverage thresholds configured in `vitest.config.ts` and passing in CI
- [ ] `onUnhandledRequest: "error"` set in MSW server setup
