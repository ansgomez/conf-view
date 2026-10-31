# -----------------------------------------------------------------------------
# Reproducible Hugo build.
#
# The Hugo compiler is pinned to an exact version (HUGO_VERSION) and downloaded
# from the official GitHub release with a verified SHA-256 checksum, on top of a
# pinned base image. That means anyone building this Dockerfile gets byte-for-byte
# the same toolchain — the build never depends on whatever Hugo happens to be
# installed on the host. To upgrade Hugo, bump HUGO_VERSION and HUGO_SHA256_*.
#
#   docker build -t confview .
#   docker run --rm -p 8000:8080 confview   # -> http://localhost:8000
# -----------------------------------------------------------------------------

# ---- stage 1: compile the static site with a pinned Hugo -------------------
# Base images are pinned by digest (multi-arch index), not just by tag; Dependabot
# (.github/dependabot.yml, docker ecosystem) proposes digest bumps.
FROM debian:bookworm-slim@sha256:99c0b25c46d0214d5a2091092e7908907dcaecd6a962cde0ac700acee222b6f3 AS build

# Extended edition (safe default even without SCSS). Pin version + checksums.
ARG HUGO_VERSION=0.165.0
ARG HUGO_SHA256_AMD64=f43494894cdf4a8630a201d5c828051c77f523cc66bb3938b30806835470ac20
ARG HUGO_SHA256_ARM64=f40ebc44dfda3896cecd3ae7ed44f5c44c4b4a30a2b7d976ece6da62da699a58

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl; \
    rm -rf /var/lib/apt/lists/*; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
      amd64) hsuffix="linux-amd64"; sum="$HUGO_SHA256_AMD64" ;; \
      arm64) hsuffix="linux-arm64"; sum="$HUGO_SHA256_ARM64" ;; \
      *) echo "unsupported architecture: $arch" >&2; exit 1 ;; \
    esac; \
    url="https://github.com/gohugoio/hugo/releases/download/v${HUGO_VERSION}/hugo_extended_${HUGO_VERSION}_${hsuffix}.tar.gz"; \
    curl -fsSL "$url" -o /tmp/hugo.tar.gz; \
    echo "${sum}  /tmp/hugo.tar.gz" | sha256sum -c -; \
    tar -xzf /tmp/hugo.tar.gz -C /usr/local/bin hugo; \
    rm /tmp/hugo.tar.gz; \
    hugo version

WORKDIR /src
COPY . .
# --gc cleans unused cache; --minify shrinks HTML/CSS/JS (JSON left intact, see hugo.toml).
RUN hugo --minify --gc --destination /public

# ---- stage 2: serve the static output --------------------------------------
# nginx-unprivileged runs as a non-root user (uid 101) and listens on 8080.
# nginx.conf adds security headers (CSP, nosniff, frame-ancestors, ...) and gzip.
FROM nginxinc/nginx-unprivileged:1.27-alpine@sha256:65e3e85dbaed8ba248841d9d58a899b6197106c23cb0ff1a132b7bfe0547e4c0
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /public /usr/share/nginx/html
EXPOSE 8080
