#!/bin/sh
# Entry point script for MikuInvidious
# Generates REDIS_PASSWORD at runtime if not provided

set -e

# Generate REDIS_PASSWORD if not set
if [ -z "$REDIS_PASSWORD" ]; then
    export REDIS_PASSWORD=$(python3 -c "import secrets; print(secrets.token_hex(32))")
    echo "Generated REDIS_PASSWORD"
fi

# Update Redis URL with password if using default
if [ -z "$REDIS_URL" ] || [ "$REDIS_URL" = "redis://redis:6379" ]; then
    export REDIS_URL="redis://:${REDIS_PASSWORD}@redis:6379"
fi

# Execute the main command
exec "$@"