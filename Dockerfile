FROM astral/uv:0.13.4-python3.15-alpine@sha256:702e9996899df9e9aad81cbf2adcda50a92f201f7ce151f15d2e5ef3ae031ca3

LABEL org.opencontainers.image.source=https://github.com/apicalshark/mikuinvidious

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 UV_NO_CACHE=1

# Install ffmpeg (used to mux DASH video+audio into a single downloadable MP4)
# build-base is a virtual build dependency for compiling sdists (e.g. brotli) on
# Python 3.15, where no prebuilt wheel exists; removed after dependency sync.
RUN apk add --no-cache ffmpeg \
    && apk add --no-cache --virtual .build-deps build-base

# Create non-root user
RUN addgroup -g 1000 appgroup && adduser -D -u 1000 -G appgroup appuser \
    && chown appuser:appgroup /app

# Install dependencies using the lockfile
COPY --chown=appuser:appgroup pyproject.toml uv.lock ./
USER appuser
RUN uv sync --frozen --no-dev --no-install-project

# Drop build dependencies now that compiled extensions are in place
USER root
RUN apk del .build-deps
USER appuser

# Copy application code
COPY --chown=appuser:appgroup . .

# Run the app from the python directory
CMD ["uv", "run", "python", "python/main.py"]
