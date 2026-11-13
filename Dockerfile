FROM astral/uv:0.13.7-python3.15-alpine@sha256:f008ab85c84befffaaa8cbde7eb2b749331769df4e18be766269d0c4904deb40
LABEL org.opencontainers.image.source=https://github.com/apicalshark/mikuinvidious

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 UV_NO_CACHE=1 UV_NO_DEV=1

# Install ffmpeg (used to mux DASH video+audio into a single downloadable MP4)
RUN apk add --no-cache ffmpeg

# Create non-root user
RUN addgroup -g 1000 appgroup && adduser -D -u 1000 -G appgroup appuser \
    && mkdir -p /app/data/img_cache \
    && chown -R appuser:appgroup /app

# Install dependencies using the lockfile
COPY --chown=appuser:appgroup pyproject.toml uv.lock ./
USER appuser
RUN uv sync --frozen --no-dev --no-install-project

# Copy application code
COPY --chown=appuser:appgroup . .

ARG APP_VERSION="dev"
ARG GIT_COMMIT=""
ENV APP_VERSION=${APP_VERSION} \
    GIT_COMMIT=${GIT_COMMIT}

# Run the app from /app
CMD ["uv", "run", "--no-dev", "python", "python/main.py"]
