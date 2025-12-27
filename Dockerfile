FROM unit:1.33.0-python3.12-slim

# Install dependencies for building or running
RUN apt-get update && apt-get install -y \
    curl \
    python3-pip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir --break-system-packages -r requirements.txt

# Copy application code
COPY . .

# Unit configuration is handled via the docker-entrypoint.sh in the base image
# if we place it in /docker-entrypoint.d/
COPY unit_config.json /docker-entrypoint.d/config.json

EXPOSE 8000

