# Fully qualified image names so the build also works with rootless Podman.

FROM docker.io/library/python:3.15-slim AS builder

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml ./
COPY src/ src/

# Install into a virtualenv so the runtime stage copies a version-independent path
# (/opt/venv) instead of /usr/local/lib/python3.X/site-packages. This lets base-image
# bumps (e.g. Dependabot) change only the FROM lines.
RUN python -m venv /opt/venv \
    && uv pip install --python /opt/venv/bin/python --no-cache .

# gcx: downloaded from the official grafana/gcx GitHub release and verified against
# the release's checksum file.
FROM docker.io/library/python:3.15-slim AS gcx

ARG GCX_VERSION=1.7.1
ARG TARGETARCH

WORKDIR /tmp/gcx

RUN set -eu; \
    arch="${TARGETARCH:-$(dpkg --print-architecture)}"; \
    base="https://github.com/grafana/gcx/releases/download/v${GCX_VERSION}"; \
    archive="gcx_${GCX_VERSION}_linux_${arch}.tar.gz"; \
    sums="gcx_${GCX_VERSION}_checksums.txt"; \
    python -c 'import sys, urllib.request as u; [u.urlretrieve(sys.argv[1] + "/" + f, f) for f in sys.argv[2:]]' \
        "$base" "$archive" "$sums"; \
    sha256sum -c --ignore-missing "$sums"; \
    tar -xzf "$archive" gcx; \
    install -m 0755 gcx /usr/local/bin/gcx; \
    /usr/local/bin/gcx version

FROM docker.io/library/python:3.15-slim

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY --from=gcx /usr/local/bin/gcx /usr/local/bin/gcx
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

RUN chmod 0755 /usr/local/bin/docker-entrypoint.sh \
    && useradd -r -s /bin/false mcp \
    && mkdir -p /var/log/grafana-mcp \
    && chown mcp /var/log/grafana-mcp
USER mcp

ENV PATH=/opt/venv/bin:$PATH
ENV MCP_TRANSPORT=http
ENV MCP_HTTP_HOST=0.0.0.0
ENV MCP_HTTP_PORT=8000

EXPOSE 8000

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
