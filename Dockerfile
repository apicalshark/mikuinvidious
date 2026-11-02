FROM astral/uv:0.13.6-python3.15-trixie-slim@sha256:60a1cfdc8945ea9d12c8fe6221bfa256b2d94df43a93cc957a503346de9ca53b
LABEL org.opencontainers.image.source=https://github.com/apicalshark/mikuinvidious

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 UV_NO_CACHE=1 UV_NO_DEV=1

# Install ffmpeg (used to mux DASH video+audio into a single downloadable MP4)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd -g 1000 appgroup && useradd -u 1000 -g appgroup -m -s /bin/bash appuser \
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

# Run the app from the python directory
CMD ["uv", "run", "--no-dev", "python", "python/main.py"]
