FROM astral/uv:python3.14-alpine@sha256:eb47c391d3a252d9d912270dd2b5e234af5493e92039fedb483f1dd1cb5659ce

LABEL org.opencontainers.image.source=https://github.com/apicalshark/mikuinvidious

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1

# Install ffmpeg (used to mux DASH video+audio into a single downloadable MP4)
RUN apk add --no-cache ffmpeg

# Install dependencies using the lockfile
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Copy application code
COPY . .

# Create non-root user and fix ownership
RUN addgroup -g 1000 appgroup && adduser -D -u 1000 -G appgroup appuser \
    && chown -R appuser:appgroup /app
USER appuser

# Run the app from the python directory
CMD ["uv", "run", "python", "python/main.py"]
