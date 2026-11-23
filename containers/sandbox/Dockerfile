# Generic image for the container sandbox (`csb create --image generic`, and the
# fallback when a repository has no usable devcontainer definition).
# Built by `csb image build --generic` and tagged claude-sandbox-generic:1
# (_sandbox_gate.GENERIC_IMAGE_REF).
#
# - Every base is pinned by its multi-arch index digest. README.md in this
#   directory has the bump procedure.
# - No VOLUME: `csb create` commits the setup container, and a volume would
#   be left out of the commit (the CLI refuses images that declare one).
# - No ENTRYPOINT / CMD / HEALTHCHECK: the CLI creates every container with
#   `--entrypoint sleep … infinity` and `--no-healthcheck`.
# - Do not add LABELs under dev.claude.sandbox*: image labels are inherited by
#   the work container, and the hook's inspect check accepts exactly the four
#   labels the CLI sets.

FROM node:24.22.3-trixie-slim@sha256:af02d57b57719b9657a45199a79264a9437940cd55b3c7b3fc99615654f124a0 AS node
FROM ghcr.io/astral-sh/uv:0.13.9@sha256:d3b601f0c7b118bc1753febf7c41cc80981df678445e0254284eecf84db79a7d AS uv

FROM debian:trixie-slim@sha256:a72b4433de1cf7d6bae0c4cee74d0547f58601a643b3375f070c51600d63b6b2

ARG DEBIAN_FRONTEND=noninteractive

# The base is digest-pinned; apt package versions follow the Debian security
# archive and are not pinned one by one.
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        git \
        procps \
        python-is-python3 \
        python3 \
        python3-dev \
        python3-pip \
        python3-venv \
    && rm -rf /var/lib/apt/lists/*

# Node and uv come from their official images. Only node itself, its bundled
# modules (npm, corepack) and its headers (for node-gyp) are copied: the node
# image's yarn links point into /opt, which is not copied. yarn and pnpm are
# corepack shims instead (they fetch the version a project pins during setup,
# while the network is connected).
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
COPY --from=node /usr/local/include/node /usr/local/include/node
COPY --from=uv /uv /uvx /usr/local/bin/

# uid / gid 1000 is the user `csb` runs every container process as. /work is the
# workspace the CLI clones the snapshot into; it must be owned by that user.
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && ln -s ../lib/node_modules/corepack/dist/corepack.js /usr/local/bin/corepack \
    && corepack enable --install-directory /usr/local/bin yarn pnpm \
    && groupadd --gid 1000 sandbox \
    && useradd --uid 1000 --gid 1000 --create-home --shell /bin/bash sandbox \
    && install -d -o 1000 -g 1000 -m 0755 /work

# The container is disposable, so `pip install` may write to the user site
# (PEP 668 would otherwise refuse it without a virtualenv).
ENV HOME=/home/sandbox \
    PATH=/home/sandbox/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    COREPACK_ENABLE_DOWNLOAD_PROMPT=0 \
    PIP_BREAK_SYSTEM_PACKAGES=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_LINK_MODE=copy \
    npm_config_update_notifier=false

USER 1000:1000
WORKDIR /work
