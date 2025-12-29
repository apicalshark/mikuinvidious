#!/bin/bash

# MikuInvidious Protocol Manager
# Usage: ./manage_quic.sh [local|testing|production]

COMPOSE_FILE="compose.yml"
SSL_DIR="./ssl"

show_usage() {
    echo "Usage: $0 [local|testing|production]"
    echo "  local      - Port 8000 only (HTTP). No SSL/QUIC."
    echo "  testing    - Enable QUIC (HTTP/3) + Self-signed certificates. Force HTTPS."
    echo "  production - Enable QUIC (HTTP/3) + Real certs. Force HTTPS + HSTS."
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

# Function to validate production certificates
validate_production_certs() {
    if [ ! -f "$SSL_DIR/cert.pem" ] || [ ! -f "$SSL_DIR/key.pem" ]; then
        echo "[ERROR] Production mode requires real certificates in $SSL_DIR/"
        echo "Please place cert.pem and key.pem in $SSL_DIR/ and try again."
        exit 1
    fi
    # Check if certificate is expired
    if ! openssl x509 -noout -checkend 0 -in "$SSL_DIR/cert.pem" > /dev/null 2>&1; then
        echo "[WARNING] The certificate in $SSL_DIR/cert.pem seems to be expired!"
    fi
}

SSL_CONF_VAL="ssl_certificate /etc/nginx/ssl/cert.pem; ssl_certificate_key /etc/nginx/ssl/key.pem; ssl_protocols TLSv1.2 TLSv1.3; ssl_early_data on;"
HSTS_VAL="add_header Strict-Transport-Security \"max-age=63072000; includeSubDomains; preload\" always;"

case "$1" in
    "local")
        echo "[-] Configuring Mode: LOCAL (HTTP Only, Port 8000)"
        sed -i 's/- ENABLE_HTTP3=.*/- ENABLE_HTTP3=false/' "$COMPOSE_FILE"
        sed -i 's|- SITE_URL=.*|- SITE_URL=http://localhost:8000|' "$COMPOSE_FILE"
        sed -i 's|- PRIMARY_LISTEN=.*|- PRIMARY_LISTEN=listen 8000;|' "$COMPOSE_FILE"
        sed -i 's|- HTTP3_ALT_SVC=.*|- HTTP3_ALT_SVC=|' "$COMPOSE_FILE"
        sed -i 's|- HSTS_HEADER=.*|- HSTS_HEADER=|' "$COMPOSE_FILE"
        sed -i 's|- SSL_CONFIG=.*|- SSL_CONFIG=|' "$COMPOSE_FILE"
        sed -i 's|^      - ./ssl:/etc/nginx/ssl:ro|      # - ./ssl:/etc/nginx/ssl:ro|' "$COMPOSE_FILE"
        echo "[!] Mode applied: Port 8000 only. Port 443 will be inactive."
        ;;
    
    "testing")
        echo "[+] Configuring Mode: TESTING (HTTPS/QUIC Only, Port 443)"
        generate_self_signed
        sed -i 's/- ENABLE_HTTP3=.*/- ENABLE_HTTP3=true/' "$COMPOSE_FILE"
        sed -i 's|- SITE_URL=.*|- SITE_URL=https://localhost|' "$COMPOSE_FILE"
        sed -i 's|- PRIMARY_LISTEN=.*|- PRIMARY_LISTEN=listen 443 quic reuseport; listen 443 ssl;|' "$COMPOSE_FILE"
        sed -i 's|- HTTP3_ALT_SVC=.*|- HTTP3_ALT_SVC=h3=\":443\"; ma=86400|' "$COMPOSE_FILE"
        sed -i 's|- HSTS_HEADER=.*|- HSTS_HEADER=|' "$COMPOSE_FILE"
        sed -i "s|- SSL_CONFIG=.*|- SSL_CONFIG=$SSL_CONF_VAL|" "$COMPOSE_FILE"
        sed -i 's|^      # - ./ssl:/etc/nginx/ssl:ro|      - ./ssl:/etc/nginx/ssl:ro|' "$COMPOSE_FILE"
        echo "[!] Mode applied: Port 443 only. Port 8000 will be inactive."
        ;;

    "production")
        echo "[+] Configuring Mode: PRODUCTION (HTTPS/QUIC Only, Port 443)"
        validate_production_certs
        sed -i 's/- ENABLE_HTTP3=.*/- ENABLE_HTTP3=true/' "$COMPOSE_FILE"
        sed -i 's|- SITE_URL=.*|- SITE_URL=https://localhost|' "$COMPOSE_FILE"
        sed -i 's|- PRIMARY_LISTEN=.*|- PRIMARY_LISTEN=listen 443 quic reuseport; listen 443 ssl;|' "$COMPOSE_FILE"
        sed -i 's|- HTTP3_ALT_SVC=.*|- HTTP3_ALT_SVC=h3=\":443\"; ma=86400|' "$COMPOSE_FILE"
        sed -i "s|- HSTS_HEADER=.*|- HSTS_HEADER=$HSTS_VAL|" "$COMPOSE_FILE"
        sed -i "s|- SSL_CONFIG=.*|- SSL_CONFIG=$SSL_CONF_VAL|" "$COMPOSE_FILE"
        sed -i 's|^      # - ./ssl:/etc/nginx/ssl:ro|      - ./ssl:/etc/nginx/ssl:ro|' "$COMPOSE_FILE"
        echo "[!] Mode applied: Port 443 only. Port 8000 will be inactive. HSTS enabled."
        ;;

    *)
        show_usage
        ;;
esac
