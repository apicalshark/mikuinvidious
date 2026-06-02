FROM astral/uv:python3.14-alpine

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1

# Install dependencies using the lockfile
COPY pyproject.toml uv.lock ./
RUN uv sync --system --frozen --no-dev --no-install-project

# Copy application code
COPY . .

# Run the app from the python directory
CMD ["python", "python/main.py"]
