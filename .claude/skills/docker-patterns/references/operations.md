# Docker operations reference

Day-to-day commands for running, inspecting and cleaning up containers, and for building images with
buildx. For writing Dockerfiles and compose files, see the sections in `SKILL.md`. For disposable
verification that must not touch the host, use `csb` ([sandbox.md](sandbox.md)) instead of the
commands here.

## What the bash-review hook treats as high-risk

The Docker daemon is root-equivalent, so the bash-review hook sends the shapes below to its
high-risk review: both models must approve, and a disagreement, a doubt or an error becomes a prompt
to the human. Under Codex there is no prompt, so what asks under Claude is blocked (exit 2).
`permissions.allow` carries `Bash(docker:*)` and `Bash(docker-compose:*)`; that allow does not bypass
the hook. The authoritative list is **Containers** in `.claude/hooks/README.md` (bash-review, step 3);
this is a summary of it.

| High-risk                                          | Examples                                                                                                                                                                                                        |
| -------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Compose start-up                                   | `docker compose up`, `run`, `create` (and `docker-compose`): compose files are not parsed                                                                                                                       |
| Other compose operations                           | `compose down -v`, `compose rm -v`, `compose cp`, `compose push`; `compose exec` as root, with `--privileged` or `--env-file`, or into `psql` / `mysql` / `mongod`                                              |
| Destructive or host-affecting                      | `docker system prune`, `volume rm`, `volume prune`, `image prune -a`, `rm -f` / `rm -v`, `cp`, `commit`, `network connect`                                                                                      |
| Leaving the machine                                | `docker push`, `image push`, `login`                                                                                                                                                                            |
| `run` / `create` that widens the container's reach | `--privileged`, host `--pid` / `--userns` / `--ipc` / `--uts` / `--cgroupns`, `--network host` or `container:*`, dangerous `--cap-add`, unconfined `--security-opt`, `--device`, `--volumes-from`, `--env-file` |
| A bind mount of a sensitive or unresolvable source | `/`, `~`, `/etc`, `docker.sock`, `~/.ssh`, `~/.aws`, `~/.docker`, `~/.claude`, a path that climbs out with `..`, or a source the hook cannot resolve (`$SRC`, `$(…)`)                                           |
| `exec` as root or into a database client           | `docker exec -u root …`, `--privileged`, `--env-file`, `psql` / `mysql` / `mongod` inside a container                                                                                                           |
| A different daemon or configuration                | `-H` / `--host`, `--context`, `--config`, a `DOCKER_HOST=` / `DOCKER_CONTEXT=` / `DOCKER_CONFIG=` prefix or `export`, `docker context use` / `create` / `update`                                                |

Not on the list, so the ordinary review applies: `docker ps`, `logs`, `inspect`, `build`, `buildx …`
(including `--push`), `compose ps` / `logs` / `config` / `build` / `down` (without `-v`),
`container prune`, `builder prune`, `image prune` without `-a`, and `rmi`. Not being on the list does
not make them harmless; `container prune` and `builder prune` still delete things, and `buildx --push`
sends an image out without the hook stopping it.

**`docker run -v "$PWD":/app …` is low-risk, and it is not a sandbox.** It mounts the host's working
tree read-write, so whatever runs inside can change your files. For verification that must not touch
the host, use `csb`.

**Leave `csb-*` alone.** Containers, networks and images that `csb` made (names starting `csb-`,
label `dev.claude.sandbox`) are operated only through `csb`. Bare `docker` that names one with `exec`,
`cp`, `network`, `commit`, `export`, `attach`, `rename` or `update`, that runs or creates with
their name or label, or that builds with their label (a `build` is looked at only for the label),
asks whatever the review says, and `docker network connect` to a `csb-net-…` network is one of them. Verbs outside that list (`stop`, `kill`, `start`, `logs`, a container named
by its ID) are not caught, which is a gap and not permission. See [sandbox.md](sandbox.md).

## Compose lifecycle

```bash
docker compose config -q              # validate only; prints nothing when the file is valid
docker compose up -d --build          # build what changed, start in the background        (high-risk)
docker compose up -d --wait           # also wait until healthchecks pass                   (high-risk)
docker compose ps                     # state of the project's services
docker compose logs -f --tail 100 api # follow one service
docker compose exec api sh            # a shell in a running service
docker compose run --rm api npm test  # a one-off container from the service definition     (high-risk)
docker compose restart api            # restart without recreating
docker compose stop                   # stop, keep containers and networks
docker compose down                   # remove containers and networks; named volumes stay
docker compose down -v                # ALSO remove the project's volumes: the data is gone  (high-risk)
```

- **`docker compose config` prints the resolved file**: values interpolated from your environment and
  from `.env`, secrets included. The output lands in the transcript, so run it with `-q` to validate,
  or add `--no-interpolate` to read the file without the values. `config --services` and
  `config --volumes` list names only.
- **`down` versus `down -v`.** `down` is safe to repeat: the next `up` finds the data in its named
  volumes. `down -v` removes the named volumes the compose file declares and the anonymous volumes
  attached to the containers, and never an `external` volume; for a `db_data` volume that is the
  database. Before running it, list what it would remove: `docker compose config --volumes`, then
  `docker volume ls --filter label=com.docker.compose.project=<name>`. The label list is a starting
  point, not a complete one. Add `--remove-orphans` to `down` to also remove containers of services
  you have deleted from the file.
- **A project is a namespace.** Containers are named `<project>-<service>-<index>` and the default
  network `<project>_default`. The project name comes from, in order, `-p`, `COMPOSE_PROJECT_NAME`,
  the top-level `name:` of the file, the name of the directory that holds the compose file, and the
  current directory. Two checkouts that resolve to the same name share one project and collide. `-f`
  can be repeated to layer override files (`compose.yaml` + `compose.override.yaml`); `--profile`
  turns on services marked with `profiles:`.
- **`up` recreates a container when its definition changed**, and keeps the one whose definition did
  not. `up --force-recreate` and `--renew-anon-volumes` change that on purpose.

## Logs and exec

```bash
docker logs -f --since 10m --tail 200 <container>   # recent output, then follow
docker exec -it <container> sh                      # a shell (use bash where the image has it)
docker exec <container> sh -c 'test -n "$NAME" && echo set || echo unset'   # one variable: set or not, never its value
docker inspect --format '{{.State.Status}} {{.State.ExitCode}} {{.State.OOMKilled}}' <container>
docker stats --no-stream                            # one CPU / memory sample
docker top <container>                              # processes inside
docker events --since 10m --until 0s --filter type=container   # what the daemon did in the last 10 minutes, then exit
```

- Do not dump a container's environment. That covers `docker exec <container> env`,
  `docker compose exec <svc> env`, `printenv`, and `docker inspect <container>` without `--format`,
  which prints the `Env` list too. The values land in the transcript. Check one name at a time, as
  above.
- `docker events` without `--until` keeps streaming and never returns, so a Bash call that runs it
  hangs until its timeout. Give it `--until`.
- `docker logs` shows what the main process wrote to stdout and stderr. A container that exits at
  once usually says why there: `docker logs <container>` works on a stopped container, and
  `docker inspect` gives its exit code. 137 is 128 + 9, SIGKILL, which `docker kill`, a stop that ran
  past its timeout and the out-of-memory killer all produce; `.State.OOMKilled` tells the last one
  apart.
- A value passed with `docker run -e` is stored in the container's configuration, so `docker inspect`
  shows it. Keep secrets out of `-e`: use a file-based secret (compose `secrets:`) or a BuildKit
  secret at build time. `--env-file` does not avoid this, and the hook treats it as high-risk.

## Cleanup

Look before deleting. Every prune command removes things you may still want.

```bash
docker system df                                   # what is using space (add -v for each item)
docker ps -a --filter status=exited                # stopped containers
docker image ls --filter dangling=true             # untagged layers left by rebuilds
docker container prune --filter "until=24h"        # stopped containers older than a day
docker image prune                                 # dangling images only
docker builder prune --filter "until=168h"         # build cache older than a week
```

| Command                 | Removes                                                                                        | Risk                                                       |
| ----------------------- | ---------------------------------------------------------------------------------------------- | ---------------------------------------------------------- |
| `docker rm <c>`         | A stopped container                                                                            | Low. `rm -f` SIGKILLs a running one first: high-risk.      |
| `docker image prune`    | Dangling images                                                                                | Low.                                                       |
| `docker image prune -a` | Every image no container uses, including the ones you pulled on purpose                        | **High-risk**: the next run re-pulls and rebuilds.         |
| `docker volume rm`      | The volumes you name                                                                           | **High-risk**: data that lived only in the volume is gone. |
| `docker volume prune`   | Anonymous volumes (Docker 23 and later; `-a` also takes unused named volumes)                  | **High-risk**: data that lived only in a volume is gone.   |
| `docker system prune`   | Stopped containers, unused networks, dangling images, build cache (`-a`, `--volumes` widen it) | **High-risk**.                                             |

Prefer a targeted removal (`docker rm`, `docker rmi <image>`, `docker volume rm <name>` after
`docker volume inspect <name>`) over a broad prune. Docker resources made by `csb` are removed with
`csb rm ID` or `csb gc`, not by pruning or by hand.

## Contexts and `DOCKER_HOST`

A context names a daemon (the local socket, an SSH host, a Docker Desktop engine). Which daemon a
command reaches depends on `-H` / `--host`, `DOCKER_HOST`, `--context`, `DOCKER_CONTEXT` and, when
none of those is set, the current context that `docker context use` chose.

```bash
docker context ls                                        # all contexts, * marks the current one
docker context show                                      # the current context (see the caveat below)
test -n "${DOCKER_HOST-}" && echo "DOCKER_HOST is set"   # an environment override, checked without printing it
docker info --format '{{.Name}} {{.OperatingSystem}}'    # which daemon actually answered
```

- Check before anything destructive. **`docker context show` reports `default` when `DOCKER_HOST` or
  `-H` redirects the CLI**, so it does not tell you where a command will really go; check
  `DOCKER_HOST` too, and let `docker info` name the daemon that answers.
- `docker context use` persists: it changes the target of every later `docker` command in every
  shell, so a forgotten `use` points `down -v` or `system prune` at another machine.
- A leftover `DOCKER_HOST` in the environment sends commands to that daemon whatever the current
  context says. If `docker ps` shows containers you do not recognise, check it first. Do not dump the
  environment with `env` or `printenv`; bash-review denies the host forms.
- `docker --context other …`, `-H …`, a `DOCKER_HOST=` prefix and `docker context use|create|update`
  are all high-risk in the hook, because they redirect work to a daemon the hook cannot see.
- `csb` takes a narrower path. It drops `DOCKER_HOST`, `DOCKER_CONTEXT`, `DOCKER_CONFIG`,
  `DOCKER_CERT_PATH` and `DOCKER_TLS*` from the environment it passes on, and takes no `-H`. At
  `create` (and `image build`) it reads the current context that `docker context use` made, once,
  with your Docker configuration, and accepts it only if it is a `unix://` socket; anything else is
  refused. It pins that endpoint and Docker binary in the sandbox's record and uses them afterwards.
  `rm` and `gc` with no record fall back to the default context.

## buildx

`docker build` is BuildKit already; `docker buildx` adds builder instances, multi-platform builds,
remote caches and `bake`.

```bash
docker buildx ls                                          # builder instances and their drivers
docker buildx build -t app:dev --load .                   # build and load into the local engine
docker buildx build --platform linux/amd64,linux/arm64 -t reg/app:1 --push .   # sends the image out
docker buildx build --target test --progress=plain .      # one stage, full step output
docker buildx build --secret id=npm_token,src=$HOME/.secrets/npm_token .
docker buildx build --cache-from type=registry,ref=reg/app:cache \
                    --cache-to type=registry,ref=reg/app:cache,mode=max .   # sends cache out
docker buildx bake                                        # the default target or group only
docker buildx bake --print                                # what it would build, without building
docker buildx imagetools inspect <image>:<tag>            # the digest of a multi-arch index
docker buildx du                                          # build cache usage
docker buildx prune --filter "until=168h"                 # trim the cache
```

- **Where the result goes.** With the default `docker` driver a build lands in the local image store.
  With a `docker-container` builder (made by `docker buildx create --use`) it does not: pass
  `--load` to bring it into the engine, `--push` to send it to a registry, or `--output` for a
  directory or an OCI archive. Loading a **multi-platform** result works when the engine uses the
  containerd image store (the default on a fresh Docker Engine 29 or later and in Docker Desktop);
  with the classic store use `--push`, or `--output type=oci,dest=app.tar`.
- **`bake`.** Without target names it builds only the `default` target or group. It reads
  `docker-bake.hcl` / `docker-bake.json`, their `.override` files and compose files in the current
  directory.
- **Pin by digest.** `docker buildx imagetools inspect <image>:<tag>` prints the digest of the
  multi-arch index; use that in `FROM image:tag@sha256:…`, not the digest of one platform's manifest.
  `containers/sandbox/README.md` follows this procedure for the generic sandbox image.
- **Cache.** `--cache-from` / `--cache-to` with `type=registry` share layers between CI runs;
  `mode=max` also exports intermediate stages. `--no-cache` ignores it for one build. The registry
  cache backend needs a `docker-container` builder, unless the engine uses the containerd image
  store, which the default `docker` driver then supports.
- **Secrets.** `--secret id=…,src=…` with `RUN --mount=type=secret,id=…` (see Security in
  `SKILL.md`). Keep the source file outside the build context.
- **What leaves the machine.** `--push` publishes an image and `--cache-to type=registry` publishes
  cache layers. The hook does not stop either (only `docker push` / `image push` are high-risk), so
  check the target and the registry login before running them. A build that runs `RUN` steps runs
  arbitrary code with the build network.
