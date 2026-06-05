FROM astral/uv:python3.14-alpine

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1

# Install dependencies using the lockfile
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Copy application code and entrypoint
COPY . .
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

# Create non-root user
RUN addgroup -g 1000 appgroup && adduser -D -u 1000 -G appgroup appuser
USER appuser

# Run the app from the python directory
ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["uv", "run", "python", "python/main.py"]
