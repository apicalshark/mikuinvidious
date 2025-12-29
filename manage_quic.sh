#!/bin/bash

# MikuInvidious Protocol Manager
# Usage: ./manage_quic.sh [local|testing|production]

COMPOSE_FILE="compose.yml"
SSL_DIR="./ssl"

show_usage() {
    echo "Usage: $0 [local|testing|production]"
    echo "  local      - Pure HTTP only (Port 8000). No SSL/QUIC."
    echo "  testing    - Enable QUIC (HTTP/3) + Self-signed certificates."
    echo "  production - Enable QUIC (HTTP/3) + Use your own certificates (Skip self-sign)."
    exit 1
}

# Function to generate self-signed certificates
generate_self_signed() {
    echo "[*] Ensuring self-signed certificates in $SSL_DIR..."
    mkdir -p "$SSL_DIR"
    if [ ! -f "$SSL_DIR/cert.pem" ] || [ ! -f "$SSL_DIR/key.pem" ]; then
        echo "[*] Generating new self-signed certificates..."
        openssl req -x509 -newkey rsa:4096 -keyout "$SSL_DIR/key.pem" -out "$SSL_DIR/cert.pem" \
            -sha256 -days 365 -nodes -subj "/CN=localhost"
    else
        echo "[*] Certificates already exist, skipping generation."
    fi
}

SSL_CONF_VAL="ssl_certificate /etc/nginx/ssl/cert.pem; ssl_certificate_key /etc/nginx/ssl/key.pem; ssl_protocols TLSv1.2 TLSv1.3; ssl_early_data on;"

case "$1" in
    "local")
        echo "[-] Switching to Mode: LOCAL (HTTP Only)"
        sed -i 's/- ENABLE_HTTP3=.*/- ENABLE_HTTP3=false/' "$COMPOSE_FILE"
        sed -i 's/- HTTP3_LISTEN=.*/- HTTP3_LISTEN=/' "$COMPOSE_FILE"
        sed -i 's/- HTTP3_ALT_SVC=.*/- HTTP3_ALT_SVC=/' "$COMPOSE_FILE"
        sed -i 's|- SSL_CONFIG=.*|- SSL_CONFIG=|' "$COMPOSE_FILE"
        # Comment out the SSL volume mount
        sed -i 's|^      - ./ssl:/etc/nginx/ssl:ro|      # - ./ssl:/etc/nginx/ssl:ro|' "$COMPOSE_FILE"
        
        echo "[*] Restarting containers..."
        docker-compose up -d
        echo "[!] Mode applied: HTTP (http://localhost:8000)"
        ;;
    
    "testing")
        echo "[+] Switching to Mode: TESTING (QUIC + Self-signed)"
        generate_self_signed
        sed -i 's/- ENABLE_HTTP3=.*/- ENABLE_HTTP3=true/' "$COMPOSE_FILE"
        sed -i 's/- HTTP3_LISTEN=.*/- HTTP3_LISTEN=listen 443 quic reuseport; listen 443 ssl;/' "$COMPOSE_FILE"
        sed -i 's/- HTTP3_ALT_SVC=.*/- HTTP3_ALT_SVC=h3=":443"; ma=86400/' "$COMPOSE_FILE"
        sed -i "s|- SSL_CONFIG=.*|- SSL_CONFIG=$SSL_CONF_VAL|" "$COMPOSE_FILE"
        # Uncomment the SSL volume mount
        sed -i 's|^      # - ./ssl:/etc/nginx/ssl:ro|      - ./ssl:/etc/nginx/ssl:ro|' "$COMPOSE_FILE"
        
        echo "[*] Restarting containers..."
        docker-compose up -d --build
        echo "[!] Mode applied: HTTP/3 Testing (https://localhost)"
        echo "[!] Note: Browser will show security warning due to self-signed cert."
        ;;

    "production")
        echo "[+] Switching to Mode: PRODUCTION (QUIC + Real Certs)"
        echo "[*] Skipping certificate generation. Ensure real certs are in $SSL_DIR/"
        sed -i 's/- ENABLE_HTTP3=.*/- ENABLE_HTTP3=true/' "$COMPOSE_FILE"
        sed -i 's/- HTTP3_LISTEN=.*/- HTTP3_LISTEN=listen 443 quic reuseport; listen 443 ssl;/' "$COMPOSE_FILE"
        sed -i 's/- HTTP3_ALT_SVC=.*/- HTTP3_ALT_SVC=h3=":443"; ma=86400/' "$COMPOSE_FILE"
        sed -i "s|- SSL_CONFIG=.*|- SSL_CONFIG=$SSL_CONF_VAL|" "$COMPOSE_FILE"
        # Uncomment the SSL volume mount
        sed -i 's|^      # - ./ssl:/etc/nginx/ssl:ro|      - ./ssl:/etc/nginx/ssl:ro|' "$COMPOSE_FILE"
        
        echo "[*] Restarting containers..."
        docker-compose up -d --build
        echo "[!] Mode applied: HTTP/3 Production (https://your-domain)"
        ;;

    *)
        show_usage
        ;;
esac