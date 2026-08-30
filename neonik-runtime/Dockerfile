FROM python:3.14-alpine3.23 AS base-os
RUN apk add --no-cache \
    gcompat \
    iproute2 \
    iputils-ping \
    openssl \
    time \
    zstd

FROM base-os AS builder-dependencies
# Build toolchain needed to compile source-only wheels (e.g. zstd) that have no
# musl/cp314 binary wheel. Only lands in this build stage, not the final image.
RUN apk add --no-cache build-base
COPY --from=ghcr.io/astral-sh/uv:0.11.1 /uv /uvx /bin/
ENV UV_PYTHON_DOWNLOADS=0 UV_LINK_MODE=copy UV_PYTHON_CACHE_DIR=/root/.cache/uv/python UV_NO_DEV=1 UV_COMPILE_BYTECODE=1 NEONIK_MPSPDZ_PATH="/mp-spdz" NEONIK_SETUP_CACHE="/root/.cache/neonik"

# Copy only the files required to install MP-SPDZ for more aggressive docker build caching.
WORKDIR /app
WORKDIR /app
# Install dependencies
COPY uv.lock pyproject.toml ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-workspace --no-editable

FROM builder-dependencies AS builder

COPY runtime /app/runtime
COPY backend/pyproject.toml /app/backend/pyproject.toml
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-editable
ENV PATH="/app/.venv/bin:$PATH"

WORKDIR /app/runtime
RUN --mount=type=cache,target=/root/.cache/neonik uv run --no-editable neonik-setup install-mpspdz --version 0.4.3

FROM base-os
ENV NEONIK_MPSPDZ_PATH="/mp-spdz" PATH="/app/.venv/bin:$PATH" UV_PYTHON_DOWNLOADS=0
COPY --from=builder --link /mp-spdz /mp-spdz
COPY --from=builder-dependencies --link /app/.venv /app/.venv
COPY --from=builder /app/.venv/ /app/.venv/
WORKDIR /app
ENTRYPOINT [ "/bin/ash" ]
