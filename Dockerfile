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
#   docker run --rm -p 8000:80 confview     # -> http://localhost:8000
# -----------------------------------------------------------------------------

# ---- stage 1: compile the static site with a pinned Hugo -------------------
# Base images are pinned by digest (multi-arch index), not just by tag; Dependabot
# (.github/dependabot.yml, docker ecosystem) proposes digest bumps.
FROM debian:bookworm-slim@sha256:99c0b25c46d0214d5a2091092e7908907dcaecd6a962cde0ac700acee222b6f3 AS build

# Extended edition (safe default even without SCSS). Pin version + checksums.
ARG HUGO_VERSION=0.140.2
ARG HUGO_SHA256_AMD64=efafaa07af0b00fcca685e2241a9ea9892960efb7d03d63e2cf5e50fd5f1f617
ARG HUGO_SHA256_ARM64=fbfcd0e3e4318bbd11d4bf7c1766c682940e1c7bbd7f012d530eb92173c7d200

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
FROM nginx:1.27-alpine@sha256:65645c7bb6a0661892a8b03b89d0743208a18dd2f3f17a54ef4b76fb8e2f2a10
COPY --from=build /public /usr/share/nginx/html
EXPOSE 80
