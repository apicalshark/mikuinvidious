# Stage 1: Build native Rust extensions
FROM astral/uv:0.13.5-python3.15-alpine@sha256:90f91872737a0e0d7672f8b7186ba5703d66b77faa223d334a2ef5f072c6bbb1 AS rust-builder
RUN apk add --no-cache cargo gcc musl-dev patchelf
WORKDIR /build
COPY crates/ crates/
RUN uv run --with "maturin>=1.15.0" maturin build --release --manifest-path crates/miku_core_rs/Cargo.toml -o /wheels

# Stage 2: Production runtime
FROM astral/uv:0.13.5-python3.15-alpine@sha256:90f91872737a0e0d7672f8b7186ba5703d66b77faa223d334a2ef5f072c6bbb1
LABEL org.opencontainers.image.source=https://github.com/apicalshark/mikuinvidious

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 UV_NO_CACHE=1 UV_NO_DEV=1

# Install ffmpeg (used to mux DASH video+audio into a single downloadable MP4)
RUN apk add --no-cache ffmpeg

# Create non-root user
RUN addgroup -g 1000 appgroup && adduser -D -u 1000 -G appgroup appuser \
    && chown appuser:appgroup /app

# Install dependencies using the lockfile
COPY --chown=appuser:appgroup pyproject.toml uv.lock ./
COPY --from=rust-builder /wheels /wheels
USER appuser
RUN uv sync --frozen --no-dev --no-install-project \
    && uv pip install /wheels/*.whl

# Copy application code
COPY --chown=appuser:appgroup . .

ARG APP_VERSION="dev"
ARG GIT_COMMIT=""
ENV APP_VERSION=${APP_VERSION} \
    GIT_COMMIT=${GIT_COMMIT}

# Run the app from the python directory
CMD ["uv", "run", "--no-dev", "python", "python/main.py"]
