---
name: security-review
description: Use this skill when adding authentication, handling user input, working with secrets, creating API endpoints, or implementing payment/sensitive features. Provides comprehensive security checklist and patterns.
---

# Security Review Skill

This skill ensures all code follows security best practices and identifies potential vulnerabilities.

## When to Activate

- Implementing authentication or authorization
- Handling user input or file uploads
- Creating new API endpoints
- Working with secrets or credentials
- Implementing payment features
- Storing or transmitting sensitive data
- Integrating third-party APIs

## Security Checklist

### 1. Secrets Management

#### ❌ NEVER Do This

```typescript
const apiKey = "sk-proj-xxxxx"; // Hardcoded secret
const dbPassword = "password123"; // In source code
```

#### ✅ ALWAYS Do This

```typescript
const apiKey = process.env.OPENAI_API_KEY;
const dbUrl = process.env.DATABASE_URL;

// Verify secrets exist
if (!apiKey) {
  throw new Error("OPENAI_API_KEY not configured");
}
```

#### Verification Steps

- [ ] No hardcoded API keys, tokens, or passwords
- [ ] All secrets in environment variables
- [ ] `.env.local` in .gitignore
- [ ] No secrets in git history
- [ ] Production secrets in hosting platform (Vercel, Railway)

### 2. Input Validation

#### Always Validate User Input

```typescript
import { z } from "zod";

// Define validation schema
const CreateUserSchema = z.object({
  email: z.string().email(),
  name: z.string().min(1).max(100),
  age: z.number().int().min(0).max(150),
});

// Validate before processing
export async function createUser(input: unknown) {
  try {
    const validated = CreateUserSchema.parse(input);
    return await db.users.create(validated);
  } catch (error) {
    if (error instanceof z.ZodError) {
      return { success: false, errors: error.errors };
    }
    throw error;
  }
}
```

#### File Upload Validation

```typescript
function validateFileUpload(file: File) {
  // Size check (5MB max)
  const maxSize = 5 * 1024 * 1024;
  if (file.size > maxSize) {
    throw new Error("File too large (max 5MB)");
  }

  // Type check
  const allowedTypes = ["image/jpeg", "image/png", "image/gif"];
  if (!allowedTypes.includes(file.type)) {
    throw new Error("Invalid file type");
  }

  // Extension check
  const allowedExtensions = [".jpg", ".jpeg", ".png", ".gif"];
  const extension = file.name.toLowerCase().match(/\.[^.]+$/)?.[0];
  if (!extension || !allowedExtensions.includes(extension)) {
    throw new Error("Invalid file extension");
  }

  return true;
}
```

#### Verification Steps

- [ ] All user inputs validated with schemas
- [ ] File uploads restricted (size, type, extension)
- [ ] No direct use of user input in queries
- [ ] Whitelist validation (not blacklist)
- [ ] Error messages don't leak sensitive info

### 3. SQL Injection Prevention

#### ❌ NEVER Concatenate SQL

```typescript
// DANGEROUS - SQL Injection vulnerability
const query = `SELECT * FROM users WHERE email = '${userEmail}'`;
await db.query(query);
```

#### ✅ ALWAYS Use Parameterized Queries

```typescript
// Safe - parameterized query
const { data } = await supabase
  .from("users")
  .select("*")
  .eq("email", userEmail);

// Or with raw SQL
await db.query("SELECT * FROM users WHERE email = $1", [userEmail]);
```

#### Verification Steps

- [ ] All database queries use parameterized queries
- [ ] No string concatenation in SQL
- [ ] ORM/query builder used correctly
- [ ] Supabase queries properly sanitized

### 4. Authentication & Authorization

#### JWT Token Handling

```typescript
// ❌ WRONG: localStorage (vulnerable to XSS)
localStorage.setItem("token", token);

// ✅ CORRECT: httpOnly cookies
res.setHeader(
  "Set-Cookie",
  `token=${token}; HttpOnly; Secure; SameSite=Strict; Max-Age=3600`,
);
```

#### Authorization Checks

```typescript
export async function deleteUser(userId: string, requesterId: string) {
  // ALWAYS verify authorization first
  const requester = await db.users.findUnique({
    where: { id: requesterId },
  });

  if (requester.role !== "admin") {
    return NextResponse.json({ error: "Unauthorized" }, { status: 403 });
  }

  // Proceed with deletion
  await db.users.delete({ where: { id: userId } });
}
```

#### Row Level Security (Supabase)

```sql
-- Enable RLS on all tables
ALTER TABLE users ENABLE ROW LEVEL SECURITY;

-- Users can only view their own data
CREATE POLICY "Users view own data"
  ON users FOR SELECT
  USING (auth.uid() = id);

-- Users can only update their own data
CREATE POLICY "Users update own data"
  ON users FOR UPDATE
  USING (auth.uid() = id);
```

#### Verification Steps

- [ ] Tokens stored in httpOnly cookies (not localStorage)
- [ ] Authorization checks before sensitive operations
- [ ] Row Level Security enabled in Supabase
- [ ] Role-based access control implemented
- [ ] Session management secure

### 5. XSS Prevention

#### Sanitize HTML

```typescript
import DOMPurify from 'isomorphic-dompurify'

// ALWAYS sanitize user-provided HTML
function renderUserContent(html: string) {
  const clean = DOMPurify.sanitize(html, {
    ALLOWED_TAGS: ['b', 'i', 'em', 'strong', 'p'],
    ALLOWED_ATTR: []
  })
  return <div dangerouslySetInnerHTML={{ __html: clean }} />
}
```

#### Content Security Policy

```typescript
// next.config.js
const securityHeaders = [
  {
    key: "Content-Security-Policy",
    value: `
      default-src 'self';
      script-src 'self' 'unsafe-eval' 'unsafe-inline';
      style-src 'self' 'unsafe-inline';
      img-src 'self' data: https:;
      font-src 'self';
      connect-src 'self' https://api.example.com;
    `
      .replace(/\s{2,}/g, " ")
      .trim(),
  },
];
```

#### Verification Steps

- [ ] User-provided HTML sanitized
- [ ] CSP headers configured
- [ ] No unvalidated dynamic content rendering
- [ ] React's built-in XSS protection used

### 6. CSRF Protection

#### CSRF Tokens

```typescript
import { csrf } from "@/lib/csrf";

export async function POST(request: Request) {
  const token = request.headers.get("X-CSRF-Token");

  if (!csrf.verify(token)) {
    return NextResponse.json({ error: "Invalid CSRF token" }, { status: 403 });
  }

  // Process request
}
```

#### SameSite Cookies

```typescript
res.setHeader(
  "Set-Cookie",
  `session=${sessionId}; HttpOnly; Secure; SameSite=Strict`,
);
```

#### Verification Steps

- [ ] CSRF tokens on state-changing operations
- [ ] SameSite=Strict on all cookies
- [ ] Double-submit cookie pattern implemented

### 7. Rate Limiting

#### API Rate Limiting

```typescript
import rateLimit from "express-rate-limit";

const limiter = rateLimit({
  windowMs: 15 * 60 * 1000, // 15 minutes
  max: 100, // 100 requests per window
  message: "Too many requests",
});

// Apply to routes
app.use("/api/", limiter);
```

#### Expensive Operations

```typescript
// Aggressive rate limiting for searches
const searchLimiter = rateLimit({
  windowMs: 60 * 1000, // 1 minute
  max: 10, // 10 requests per minute
  message: "Too many search requests",
});

app.use("/api/search", searchLimiter);
```

#### Verification Steps

- [ ] Rate limiting on all API endpoints
- [ ] Stricter limits on expensive operations
- [ ] IP-based rate limiting
- [ ] User-based rate limiting (authenticated)

### 8. Sensitive Data Exposure

#### Logging

```typescript
// ❌ WRONG: Logging sensitive data
console.log("User login:", { email, password });
console.log("Payment:", { cardNumber, cvv });

// ✅ CORRECT: Redact sensitive data
console.log("User login:", { email, userId });
console.log("Payment:", { last4: card.last4, userId });
```

#### Error Messages

```typescript
// ❌ WRONG: Exposing internal details
catch (error) {
  return NextResponse.json(
    { error: error.message, stack: error.stack },
    { status: 500 }
  )
}

// ✅ CORRECT: Generic error messages
catch (error) {
  console.error('Internal error:', error)
  return NextResponse.json(
    { error: 'An error occurred. Please try again.' },
    { status: 500 }
  )
}
```

#### Verification Steps

- [ ] No passwords, tokens, or secrets in logs
- [ ] Error messages generic for users
- [ ] Detailed errors only in server logs
- [ ] No stack traces exposed to users

### 9. Cryptographic Signature & Transaction Integrity

Applies to any feature that trusts a client-provided signature or submits
value-bearing operations (signed webhooks, sign-in-with-signature, payments).

#### Signature / Ownership Verification

```typescript
import nacl from "tweetnacl";

// Verify a client-provided signature actually proves control of the key /
// identity it claims — never trust the claim without checking it server-side.
function verifySignatureOwnership(
  publicKey: string,
  signature: string,
  message: string,
) {
  try {
    return nacl.sign.detached.verify(
      Buffer.from(message),
      Buffer.from(signature, "base64"),
      Buffer.from(publicKey, "base64"),
    );
  } catch (error) {
    return false;
  }
}
```

#### Transaction Verification

```typescript
async function verifyTransaction(transaction: Transaction) {
  // Verify recipient
  if (transaction.to !== expectedRecipient) {
    throw new Error("Invalid recipient");
  }

  // Verify amount
  if (transaction.amount > maxAmount) {
    throw new Error("Amount exceeds limit");
  }

  // Verify user has sufficient balance
  const balance = await getBalance(transaction.from);
  if (balance < transaction.amount) {
    throw new Error("Insufficient balance");
  }

  return true;
}
```

#### Verification Steps

- [ ] Client-provided signatures verified server-side
- [ ] Transaction details validated (recipient, amount)
- [ ] Balance / limit checks before value-bearing operations
- [ ] No blind trust of client-submitted transaction data

### 10. Dependency Security

#### Regular Updates

```bash
# Check for vulnerabilities
npm audit

# Fix automatically fixable issues
npm audit fix

# Update dependencies
npm update

# Check for outdated packages
npm outdated
```

#### Lock Files

```bash
# ALWAYS commit lock files
git add package-lock.json

# Use in CI/CD for reproducible builds
npm ci  # Instead of npm install
```

#### Verification Steps

- [ ] Dependencies up to date
- [ ] No known vulnerabilities (npm audit clean)
- [ ] Lock files committed
- [ ] Dependabot enabled on GitHub
- [ ] Regular security updates

### 11. Command Injection Prevention

#### ❌ NEVER Pass User Input to Shell

```typescript
import { exec } from "child_process";

// DANGEROUS - command injection vulnerability
exec(`ping ${userInput}`, callback);
```

#### ✅ ALWAYS Use Libraries or Argument Arrays

```typescript
// Use a library instead of shelling out
import dns from "dns";
dns.lookup(userInput, callback);

// If a subprocess is unavoidable, pass args as an array (no shell)
import { execFile } from "child_process";
execFile("ping", ["-c", "1", validatedHost], callback);
```

#### Verification Steps

- [ ] No user input concatenated into shell commands
- [ ] `execFile`/spawn with argument arrays instead of `exec`
- [ ] Input validated against a whitelist before subprocess use

### 12. SSRF Prevention

#### ❌ NEVER Fetch User-Provided URLs Directly

```typescript
// DANGEROUS - SSRF vulnerability (can reach internal services)
const response = await fetch(userProvidedUrl);
```

#### ✅ ALWAYS Validate and Whitelist URLs

```typescript
const allowedDomains = ["api.example.com", "cdn.example.com"];
const url = new URL(userProvidedUrl);

if (!allowedDomains.includes(url.hostname)) {
  throw new Error("Invalid URL");
}

const response = await fetch(url.toString());
```

#### Verification Steps

- [ ] User-provided URLs validated against a domain whitelist
- [ ] Internal/private IP ranges blocked (169.254.x.x, 10.x.x.x, etc.)
- [ ] Redirects not followed blindly

### 13. Race Conditions in Critical Operations

#### ❌ NEVER Check-Then-Act Without Locking

```typescript
// DANGEROUS - parallel requests can both pass the check
const balance = await getBalance(userId);
if (balance >= amount) {
  await withdraw(userId, amount);
}
```

#### ✅ ALWAYS Use Atomic Transactions with Locks

```typescript
await db.transaction(async (trx) => {
  const balance = await trx("balances")
    .where({ user_id: userId })
    .forUpdate() // Lock row
    .first();

  if (balance.amount < amount) {
    throw new Error("Insufficient balance");
  }

  await trx("balances").where({ user_id: userId }).decrement("amount", amount);
});
```

#### Verification Steps

- [ ] Balance/quota checks and updates are atomic (single transaction)
- [ ] Row locks (`FOR UPDATE`) on read-modify-write paths
- [ ] No floating-point arithmetic for money
- [ ] Idempotency keys on state-changing endpoints

## Security Scanning Commands

```bash
# Check for vulnerable dependencies
npm audit
npm audit --audit-level=high

# Static analysis for security issues
npx eslint . --plugin security

# Scan for hardcoded secrets
grep -rE "api[_-]?key|password|secret|token" --include="*.ts" --include="*.js" --include="*.json" .
npx trufflehog filesystem . --json

# Check git history for secrets
git log -p | grep -iE "password|api_key|secret"

# Pattern-based scanning
semgrep --config auto .
```

## Common False Positives

Not every finding is a vulnerability — always verify context before flagging:

- Environment variables in `.env.example` (placeholders, not actual secrets)
- Test credentials in test files (if clearly marked as fake)
- Public API keys (if actually meant to be public, e.g. Stripe publishable keys)
- SHA256/MD5 used for checksums (not password hashing)

## Security Testing

### Automated Security Tests

```typescript
// Test authentication
test("requires authentication", async () => {
  const response = await fetch("/api/protected");
  expect(response.status).toBe(401);
});

// Test authorization
test("requires admin role", async () => {
  const response = await fetch("/api/admin", {
    headers: { Authorization: `Bearer ${userToken}` },
  });
  expect(response.status).toBe(403);
});

// Test input validation
test("rejects invalid input", async () => {
  const response = await fetch("/api/users", {
    method: "POST",
    body: JSON.stringify({ email: "not-an-email" }),
  });
  expect(response.status).toBe(400);
});

// Test rate limiting
test("enforces rate limits", async () => {
  const requests = Array(101)
    .fill(null)
    .map(() => fetch("/api/endpoint"));

  const responses = await Promise.all(requests);
  const tooManyRequests = responses.filter((r) => r.status === 429);

  expect(tooManyRequests.length).toBeGreaterThan(0);
});
```

## Pre-Deployment Security Checklist

Before ANY production deployment:

- [ ] **Secrets**: No hardcoded secrets, all in env vars
- [ ] **Input Validation**: All user inputs validated
- [ ] **SQL Injection**: All queries parameterized
- [ ] **XSS**: User content sanitized
- [ ] **CSRF**: Protection enabled
- [ ] **Authentication**: Proper token handling
- [ ] **Authorization**: Role checks in place
- [ ] **Rate Limiting**: Enabled on all endpoints
- [ ] **HTTPS**: Enforced in production
- [ ] **Security Headers**: CSP, X-Frame-Options configured
- [ ] **Error Handling**: No sensitive data in errors
- [ ] **Logging**: No sensitive data logged
- [ ] **Dependencies**: Up to date, no vulnerabilities
- [ ] **Row Level Security**: Enabled in Supabase
- [ ] **CORS**: Properly configured
- [ ] **File Uploads**: Validated (size, type)
- [ ] **Signatures**: Client-provided signatures verified server-side (payments, signed webhooks)

## Resources

- [OWASP Top 10](https://owasp.org/www-project-top-ten/)
- [Next.js Security](https://nextjs.org/docs/security)
- [Supabase Security](https://supabase.com/docs/guides/auth)
- [Web Security Academy](https://portswigger.net/web-security)
- クラウドインフラ (IAM / ネットワーク / IaC / コンテナ / ログ監査) のレビュー観点は
  同ディレクトリの [cloud-infrastructure-security.md](cloud-infrastructure-security.md) を参照
