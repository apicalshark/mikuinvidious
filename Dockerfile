FROM astral/uv:0.13.1-python3.14-alpine@sha256:513b826406ed7efc2604acdf4935266e020283ce17e2ea96509cd4f1a68b9f1a

LABEL org.opencontainers.image.source=https://github.com/apicalshark/mikuinvidious

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 UV_NO_CACHE=1

# Install ffmpeg (used to mux DASH video+audio into a single downloadable MP4)
RUN apk add --no-cache ffmpeg

# Create non-root user
RUN addgroup -g 1000 appgroup && adduser -D -u 1000 -G appgroup appuser \
    && chown appuser:appgroup /app

# Install dependencies using the lockfile
COPY --chown=appuser:appgroup pyproject.toml uv.lock ./
USER appuser
RUN uv sync --frozen --no-dev --no-install-project

# Copy application code
COPY --chown=appuser:appgroup . .

# Run the app from the python directory
CMD ["uv", "run", "python", "python/main.py"]
