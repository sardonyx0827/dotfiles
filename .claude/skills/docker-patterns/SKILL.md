---
name: docker-patterns
description: >
  Docker and container best practices for writing efficient, secure, production-ready images.
  Use this skill whenever you are writing or editing a Dockerfile or docker-compose.yml,
  configuring devcontainers, debugging a failing container build, shrinking image size,
  speeding up build times, or hardening a container against security vulnerabilities.
  If Docker is in scope, load this skill first.
---

# Docker Patterns

Container best practices for fast builds, small images, and secure runtimes.

## 1. Multi-Stage Builds

Use separate build and runtime stages. The runtime stage should contain only the compiled
artifact — never the toolchain, source code, or build-time dependencies. This is the
single biggest lever for reducing final image size.

```dockerfile
# ❌ WRONG: single stage ships the full Node.js toolchain and source
FROM node:20
WORKDIR /app
COPY . .
RUN npm ci && npm run build
CMD ["node", "dist/server.js"]

# ✅ CORRECT: build stage compiles; runtime stage ships only the artifact
FROM node:20-slim AS builder
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM node:20-slim AS runtime
WORKDIR /app
COPY --from=builder /app/dist ./dist
COPY --from=builder /app/node_modules ./node_modules
CMD ["node", "dist/server.js"]
```

Go example — the runtime image can be distroless or scratch because Go produces a
statically-linked binary:

```dockerfile
FROM golang:1.22 AS builder
WORKDIR /app
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -o /app/server ./cmd/server

FROM gcr.io/distroless/static-debian12 AS runtime
COPY --from=builder /app/server /server
USER nonroot:nonroot
ENTRYPOINT ["/server"]
```

## 2. Layer Caching

Order instructions from least-volatile to most-volatile. Docker invalidates every layer
below the first changed layer; a misplaced `COPY . .` before `npm ci` means dependencies
are re-downloaded on every source change.

```dockerfile
# ❌ WRONG: source COPY comes before install — cache busted on every file change
FROM node:20-slim
WORKDIR /app
COPY . .
RUN npm ci
RUN npm run build

# ✅ CORRECT: manifest files first, source last
FROM node:20-slim
WORKDIR /app
COPY package.json package-lock.json ./   # changes rarely
RUN npm ci                                # cached until manifests change
COPY . .                                  # changes often — only invalidates build step
RUN npm run build
```

The rule: `COPY <manifest> → RUN install → COPY <source> → RUN build`.

## 3. .dockerignore

Always ship a `.dockerignore`. Without it, `COPY . .` pulls `node_modules`, `.git`,
`.env`, and build artifacts into the build context, slowing transfers and — critically —
baking secrets into intermediate layers where `docker history` can expose them.

```
# .dockerignore
node_modules
.git
.env
.env.*
dist
coverage
*.log
.DS_Store
```

For Go projects also ignore:

```
vendor        # if not vendoring intentionally
*.test
*.out
```

## 4. Base Image Choice

Pin to a specific minor version, not `latest`. `latest` silently changes on upstream
pushes, breaking reproducibility. For maximum auditability, pin by digest.

```dockerfile
# ❌ WRONG: unpinned tag
FROM node:latest
FROM node:20

# ✅ BETTER: pinned minor version
FROM node:20.14-slim

# ✅ BEST: pinned by digest (immune to tag mutation)
FROM node:20.14-slim@sha256:abc123...
```

**slim vs alpine tradeoffs**

| Image             | Pros                                  | Cons                                                    |
| ----------------- | ------------------------------------- | ------------------------------------------------------- |
| `*-slim` (Debian) | glibc — broad binary compatibility    | ~80 MB vs ~5 MB                                         |
| `*-alpine`        | tiny, fast pulls                      | musl libc — native modules may fail (`bcrypt`, `sharp`) |
| distroless        | no shell attack surface; minimal CVEs | harder to debug interactively                           |

Use `distroless` for compiled binaries (Go, Rust). Use `slim` for Node.js unless you
have confirmed all native deps work under musl.

## 5. Security

### Non-root USER

Containers run as root by default. A container escape then yields host root.
Always drop privileges before the final `CMD`.

```dockerfile
# ✅ CORRECT: create a dedicated user and switch to it
RUN addgroup --system appgroup && adduser --system --ingroup appgroup appuser
USER appuser
CMD ["node", "dist/server.js"]
```

### Never pass secrets via ARG or ENV

`ARG` and `ENV` values persist in image layer metadata and are visible via
`docker history`. Use BuildKit secret mounts instead — they are never written to any layer.

```dockerfile
# ❌ WRONG: secret visible in docker history
ARG NPM_TOKEN
RUN echo "//registry.npmjs.org/:_authToken=${NPM_TOKEN}" > ~/.npmrc && npm ci

# ✅ CORRECT: BuildKit secret mount — zero trace in image history
# syntax=docker/dockerfile:1
RUN --mount=type=secret,id=npm_token \
    NPM_TOKEN=$(cat /run/secrets/npm_token) \
    echo "//registry.npmjs.org/:_authToken=${NPM_TOKEN}" > ~/.npmrc && \
    npm ci && \
    rm ~/.npmrc
```

Build with: `docker build --secret id=npm_token,src=.npmrc .`

### Read-only filesystem

```dockerfile
# docker-compose.yml
services:
  api:
    image: myapp
    read_only: true
    tmpfs:
      - /tmp          # writable scratch space when needed
```

### Scan images

```bash
docker scout cves myapp:latest
trivy image myapp:latest
```

## 6. Runtime Hygiene

### HEALTHCHECK

Without a HEALTHCHECK, Docker marks a container healthy as soon as the process starts,
even if the app hasn't bound its port yet. Define one so orchestrators can route traffic
only to genuinely ready replicas.

```dockerfile
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -f http://localhost:3000/health || exit 1
```

### Signal Handling — exec-form CMD and tini

Shell-form `CMD` (`CMD node server.js`) spawns a shell as PID 1. Signals sent by Docker
(`SIGTERM`) go to the shell, not the app, causing unclean shutdowns. Use exec-form or add
`tini` as a minimal init process.

```dockerfile
# ❌ WRONG: shell form — SIGTERM goes to sh, not node
CMD node dist/server.js

# ✅ CORRECT: exec form — process is PID 1, receives signals directly
CMD ["node", "dist/server.js"]

# ✅ ALSO CORRECT: tini handles zombie reaping and signal forwarding
RUN apk add --no-cache tini
ENTRYPOINT ["/sbin/tini", "--"]
CMD ["node", "dist/server.js"]
```

In docker-compose you can use `init: true` instead of installing tini manually.

### One process per container

Do not run a database, cache, and app server in a single container. Use
docker-compose services or Kubernetes pods to compose processes — it simplifies
logging, scaling, and failure isolation.

## 7. docker-compose for Development

```yaml
# docker-compose.yml
services:
  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_PASSWORD: secret
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 5s
      timeout: 3s
      retries: 5
    volumes:
      - db_data:/var/lib/postgresql/data # named volume — survives restarts

  api:
    build: .
    env_file: .env # keep secrets out of compose file
    ports:
      - "3000:3000"
    depends_on:
      db:
        condition: service_healthy # wait for real readiness, not just startup
    volumes:
      - .:/app # bind mount for hot-reload in dev
      - /app/node_modules # exclude host node_modules from overlay
    init: true

  redis:
    image: redis:7-alpine
    profiles:
      - cache # opt-in: docker compose --profile cache up

volumes:
  db_data:
```

Key rules:

- Use `condition: service_healthy` in `depends_on`, not just the service name.
- Use `env_file` to load secrets; never hardcode them inline.
- Use named volumes for persistent data; bind mounts only for live-reload source.
- Use `profiles` to make optional services (workers, redis) opt-in.

## 8. Debugging

```bash
# See every build step with full output — find the failing layer
docker build --progress=plain --no-cache .

# Drop into a shell in the failing stage
docker build --target builder -t debug-image .
docker run -it --entrypoint sh debug-image

# Inspect a running container's env, mounts, and network
docker inspect <container_id>

# Stream logs
docker logs -f <container_id>

# Analyze layer sizes — find what's bloating the image
dive myapp:latest

# Execute a one-off command in a running container
docker exec -it <container_id> sh
```

## Checklist

Before shipping a Dockerfile or docker-compose change:

- [ ] Multi-stage build: runtime stage contains only the compiled artifact
- [ ] Dependency manifest copied and installed before source COPY
- [ ] `.dockerignore` excludes `node_modules`, `.git`, `.env`, build artifacts
- [ ] Base image pinned to a minor version (or digest), not `latest`
- [ ] Non-root `USER` set before `CMD`
- [ ] No secrets passed via `ARG` or `ENV`; use BuildKit `--mount=type=secret`
- [ ] `HEALTHCHECK` defined
- [ ] `CMD` uses exec-form or `tini`/`init: true` for signal handling
- [ ] docker-compose `depends_on` uses `condition: service_healthy`
- [ ] Image scanned with `docker scout` or `trivy` before promotion
