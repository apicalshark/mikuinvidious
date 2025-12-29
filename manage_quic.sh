#!/bin/bash

# MikuInvidious QUIC/HTTP3 Manager
# Usage: ./manage_quic.sh [on|off]

COMPOSE_FILE="compose.yml"
SSL_DIR="./ssl"

show_usage() {
    echo "Usage: $0 [on|off]"
    echo "  on  - Generate self-signed certs and enable HTTP/3 (QUIC)"
    echo "  off - Disable HTTP/3 and return to standard HTTP"
    exit 1
}

if [ "$1" == "on" ]; then
    echo "[+] Enabling QUIC and Self-signed SSL..."

    # 1. Generate SSL certificates if they don't exist
    if [ ! -f "$SSL_DIR/cert.pem" ]; then
        echo "[*] Generating self-signed certificates..."
        mkdir -p "$SSL_DIR"
        openssl req -x509 -newkey rsa:4096 -keyout "$SSL_DIR/key.pem" -out "$SSL_DIR/cert.pem" \
            -sha256 -days 365 -nodes -subj "/CN=localhost"
    fi

    # 2. Update compose.yml using sed
    # Enable HTTP3 in app service
    sed -i 's/- ENABLE_HTTP3=false/- ENABLE_HTTP3=true/' "$COMPOSE_FILE"
    
    # Enable HTTP3 in nginx service
    sed -i 's/- HTTP3_LISTEN=.*/- HTTP3_LISTEN=listen 443 quic reuseport; listen 443 ssl;/' "$COMPOSE_FILE"
    sed -i 's/- HTTP3_ALT_SVC=.*/- HTTP3_ALT_SVC=h3=":443"; ma=86400/' "$COMPOSE_FILE"

    echo "[*] Restarting containers..."
    docker-compose up -d --build

    echo "[!] QUIC is now ENABLED."
    echo "[!] Access via https://localhost (Accept the self-signed certificate warning)"

elif [ "$1" == "off" ]; then
    echo "[-] Disabling QUIC..."

    # Update compose.yml using sed
    sed -i 's/- ENABLE_HTTP3=true/- ENABLE_HTTP3=false/' "$COMPOSE_FILE"
    
    # Disable HTTP3 in nginx service (clear values)
    sed -i 's/- HTTP3_LISTEN=.*/- HTTP3_LISTEN=/' "$COMPOSE_FILE"
    sed -i 's/- HTTP3_ALT_SVC=.*/- HTTP3_ALT_SVC=/' "$COMPOSE_FILE"

    echo "[*] Restarting containers..."
    docker-compose up -d

    echo "[!] QUIC is now DISABLED. Standard access via http://localhost:8000"

else
    show_usage
fi
