FROM astral/uv:0.13.5-python3.15-trixie-slim@sha256:86a05dd8f19b21368fbb160c7e2ad0576780bebbe89114b6deeba2506f7612e1
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
