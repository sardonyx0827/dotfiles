---
name: coding-standards
description: Universal coding standards for TypeScript, JavaScript, React, and Node.js — naming, readability, KISS/DRY, error handling, and structure. Use this skill whenever writing or reviewing TS/JS/React/Node code and no framework-specific skill (backend/frontend/testing) fully covers the change, or when you need a baseline for consistent, readable, well-structured code.
---

# Coding Standards & Best Practices

Universal coding standards applicable across all projects.

## Code Quality Principles

### 1. Readability First

- Code is read more than written
- Clear variable and function names
- Self-documenting code preferred over comments
- Consistent formatting

### 2. KISS (Keep It Simple, Stupid)

- Simplest solution that works
- Avoid over-engineering
- No premature optimization
- Easy to understand > clever code

### 3. DRY (Don't Repeat Yourself)

- Extract common logic into functions
- Create reusable components
- Share utilities across modules
- Avoid copy-paste programming

### 4. YAGNI (You Aren't Gonna Need It)

- Don't build features before they're needed
- Avoid speculative generality
- Add complexity only when required
- Start simple, refactor when needed

## TypeScript/JavaScript Standards

### Variable Naming

```typescript
// ✅ GOOD: Descriptive names
const productSearchQuery = "wireless";
const isUserAuthenticated = true;
const totalRevenue = 1000;

// ❌ BAD: Unclear names
const q = "wireless";
const flag = true;
const x = 1000;
```

### Function Naming

```typescript
// ✅ GOOD: Verb-noun pattern
async function fetchProductData(productId: string) {}
function calculateSimilarity(a: number[], b: number[]) {}
function isValidEmail(email: string): boolean {}

// ❌ BAD: Unclear or noun-only
async function product(id: string) {}
function similarity(a, b) {}
function email(e) {}
```

### Immutability Pattern (CRITICAL)

```typescript
// ✅ ALWAYS use spread operator
const updatedUser = {
  ...user,
  name: "New Name",
};

const updatedArray = [...items, newItem];

// ❌ NEVER mutate directly
user.name = "New Name"; // BAD
items.push(newItem); // BAD
```

### Error Handling

```typescript
// ✅ GOOD: Comprehensive error handling
async function fetchData(url: string) {
  try {
    const response = await fetch(url);

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}: ${response.statusText}`);
    }

    return await response.json();
  } catch (error) {
    console.error("Fetch failed:", error);
    throw new Error("Failed to fetch data");
  }
}

// ❌ BAD: No error handling
async function fetchData(url) {
  const response = await fetch(url);
  return response.json();
}
```

### Async/Await Best Practices

```typescript
// ✅ GOOD: Parallel execution when possible
const [users, products, stats] = await Promise.all([
  fetchUsers(),
  fetchProducts(),
  fetchStats(),
]);

// ❌ BAD: Sequential when unnecessary
const users = await fetchUsers();
const products = await fetchProducts();
const stats = await fetchStats();
```

### Type Safety

```typescript
// ✅ GOOD: Proper types
interface Product {
  id: string;
  name: string;
  status: "active" | "resolved" | "closed";
  created_at: Date;
}

function getProduct(id: string): Promise<Product> {
  // Implementation
}

// ❌ BAD: Using 'any'
function getProduct(id: any): Promise<any> {
  // Implementation
}
```

## React Best Practices

### Component Structure

```typescript
// ✅ GOOD: Functional component with types
interface ButtonProps {
  children: React.ReactNode
  onClick: () => void
  disabled?: boolean
  variant?: 'primary' | 'secondary'
}

export function Button({
  children,
  onClick,
  disabled = false,
  variant = 'primary'
}: ButtonProps) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={`btn btn-${variant}`}
    >
      {children}
    </button>
  )
}

// ❌ BAD: No types, unclear structure
export function Button(props) {
  return <button onClick={props.onClick}>{props.children}</button>
}
```

### Custom Hooks

```typescript
// ✅ GOOD: Reusable custom hook
export function useDebounce<T>(value: T, delay: number): T {
  const [debouncedValue, setDebouncedValue] = useState<T>(value);

  useEffect(() => {
    const handler = setTimeout(() => {
      setDebouncedValue(value);
    }, delay);

    return () => clearTimeout(handler);
  }, [value, delay]);

  return debouncedValue;
}

// Usage
const debouncedQuery = useDebounce(searchQuery, 500);
```

### State Management

```typescript
// ✅ GOOD: Proper state updates
const [count, setCount] = useState(0);

// Functional update for state based on previous state
setCount((prev) => prev + 1);

// ❌ BAD: Direct state reference
setCount(count + 1); // Can be stale in async scenarios
```

### Conditional Rendering

```typescript
// ✅ GOOD: Clear conditional rendering
{isLoading && <Spinner />}
{error && <ErrorMessage error={error} />}
{data && <DataDisplay data={data} />}

// ❌ BAD: Ternary hell
{isLoading ? <Spinner /> : error ? <ErrorMessage error={error} /> : data ? <DataDisplay data={data} /> : null}
```

## API Design Standards

### REST API Conventions

```
GET    /api/products              # List all products
GET    /api/products/:id          # Get specific product
POST   /api/products              # Create new product
PUT    /api/products/:id          # Update product (full)
PATCH  /api/products/:id          # Update product (partial)
DELETE /api/products/:id          # Delete product

# Query parameters for filtering
GET /api/products?status=active&limit=10&offset=0
```

### Response Format

```typescript
// ✅ GOOD: Consistent response structure
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

// Success response
return NextResponse.json({
  success: true,
  data: products,
  meta: { total: 100, page: 1, limit: 10 },
});

// Error response
return NextResponse.json(
  {
    success: false,
    error: "Invalid request",
  },
  { status: 400 },
);
```

### Input Validation

```typescript
import { z } from "zod";

// ✅ GOOD: Schema validation
const CreateProductSchema = z.object({
  name: z.string().min(1).max(200),
  description: z.string().min(1).max(2000),
  endDate: z.string().datetime(),
  categories: z.array(z.string()).min(1),
});

export async function POST(request: Request) {
  const body = await request.json();

  try {
    const validated = CreateProductSchema.parse(body);
    // Proceed with validated data
  } catch (error) {
    if (error instanceof z.ZodError) {
      return NextResponse.json(
        {
          success: false,
          error: "Validation failed",
          details: error.errors,
        },
        { status: 400 },
      );
    }
  }
}
```

## File Organization

### Project Structure

```
src/
├── app/                    # Next.js App Router
│   ├── api/               # API routes
│   ├── products/           # Product pages
│   └── (auth)/           # Auth pages (route groups)
├── components/            # React components
│   ├── ui/               # Generic UI components
│   ├── forms/            # Form components
│   └── layouts/          # Layout components
├── hooks/                # Custom React hooks
├── lib/                  # Utilities and configs
│   ├── api/             # API clients
│   ├── utils/           # Helper functions
│   └── constants/       # Constants
├── types/                # TypeScript types
└── styles/              # Global styles
```

### File Naming

```
components/Button.tsx          # PascalCase for components
hooks/useAuth.ts              # camelCase with 'use' prefix
lib/formatDate.ts             # camelCase for utilities
types/product.types.ts         # camelCase with .types suffix
```

## Comments & Documentation

### When to Comment

```typescript
// ✅ GOOD: Explain WHY, not WHAT
// Use exponential backoff to avoid overwhelming the API during outages
const delay = Math.min(1000 * Math.pow(2, retryCount), 30000);

// Deliberately using mutation here for performance with large arrays
items.push(newItem);

// ❌ BAD: Stating the obvious
// Increment counter by 1
count++;

// Set name to user's name
name = user.name;
```

### JSDoc for Public APIs

````typescript
/**
 * Searches products using semantic similarity.
 *
 * @param query - Natural language search query
 * @param limit - Maximum number of results (default: 10)
 * @returns Array of products sorted by similarity score
 * @throws {Error} If OpenAI API fails or Redis unavailable
 *
 * @example
 * ```typescript
 * const results = await searchProducts('wireless', 5)
 * console.log(results[0].name) // "Trump vs Biden"
 * ```
 */
export async function searchProducts(
  query: string,
  limit: number = 10,
): Promise<Product[]> {
  // Implementation
}
````

## Performance Best Practices

### Memoization

```typescript
import { useMemo, useCallback } from "react";

// ✅ GOOD: Memoize expensive computations
const sortedProducts = useMemo(() => {
  return products.sort((a, b) => b.sales - a.sales);
}, [products]);

// ✅ GOOD: Memoize callbacks
const handleSearch = useCallback((query: string) => {
  setSearchQuery(query);
}, []);
```

### Lazy Loading

```typescript
import { lazy, Suspense } from 'react'

// ✅ GOOD: Lazy load heavy components
const HeavyChart = lazy(() => import('./HeavyChart'))

export function Dashboard() {
  return (
    <Suspense fallback={<Spinner />}>
      <HeavyChart />
    </Suspense>
  )
}
```

### Database Queries

```typescript
// ✅ GOOD: Select only needed columns
const { data } = await supabase
  .from("products")
  .select("id, name, status")
  .limit(10);

// ❌ BAD: Select everything
const { data } = await supabase.from("products").select("*");
```

## Testing Standards

### Test Structure (AAA Pattern)

```typescript
test("calculates similarity correctly", () => {
  // Arrange
  const vector1 = [1, 0, 0];
  const vector2 = [0, 1, 0];

  // Act
  const similarity = calculateCosineSimilarity(vector1, vector2);

  // Assert
  expect(similarity).toBe(0);
});
```

### Test Naming

```typescript
// ✅ GOOD: Descriptive test names
test("returns empty array when no products match query", () => {});
test("throws error when OpenAI API key is missing", () => {});
test("falls back to substring search when Redis unavailable", () => {});

// ❌ BAD: Vague test names
test("works", () => {});
test("test search", () => {});
```

## Code Smell Detection

Watch for these anti-patterns:

### 1. Long Functions

```typescript
// ❌ BAD: Function > 50 lines
function processProductData() {
  // 100 lines of code
}

// ✅ GOOD: Split into smaller functions
function processProductData() {
  const validated = validateData();
  const transformed = transformData(validated);
  return saveData(transformed);
}
```

### 2. Deep Nesting

```typescript
// ❌ BAD: 5+ levels of nesting
if (user) {
  if (user.isAdmin) {
    if (product) {
      if (product.isActive) {
        if (hasPermission) {
          // Do something
        }
      }
    }
  }
}

// ✅ GOOD: Early returns
if (!user) return;
if (!user.isAdmin) return;
if (!product) return;
if (!product.isActive) return;
if (!hasPermission) return;

// Do something
```

### 3. Magic Numbers

```typescript
// ❌ BAD: Unexplained numbers
if (retryCount > 3) {
}
setTimeout(callback, 500);

// ✅ GOOD: Named constants
const MAX_RETRIES = 3;
const DEBOUNCE_DELAY_MS = 500;

if (retryCount > MAX_RETRIES) {
}
setTimeout(callback, DEBOUNCE_DELAY_MS);
```

**Remember**: Code quality is not negotiable. Clear, maintainable code enables rapid development and confident refactoring.
