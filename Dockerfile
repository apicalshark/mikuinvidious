FROM astral/uv:python3.14-alpine

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1

COPY pyproject.toml ./

RUN uv pip install --system --no-cache -r pyproject.toml

# Copy application code
COPY . .

# Run the app from the python directory
CMD ["/usr/local/bin/granian", "--interface", "asgi", "--host", "0.0.0.0", "--port", "8080", "--working-dir", "python", "main:app"]