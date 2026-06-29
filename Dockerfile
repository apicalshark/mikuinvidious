LABEL org.opencontainers.image.source=https://github.com/apicalshark/mikuinvidious

FROM astral/uv:python3.14-alpine

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1

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
