FROM astral/uv:python3.14-alpine

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1

COPY pyproject.toml ./

RUN uv pip install --system --no-cache -r pyproject.toml

# Copy application code
COPY . .

# Run the app from the python directory
CMD ["python", "python/main.py"]