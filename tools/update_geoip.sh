#!/usr/bin/env bash
#
# update_geoip.sh - Fetch and atomically update MaxMind GeoLite2 databases from P3TERX/GeoLite.mmdb
#
# NOTE: Anubis memory-maps .mmdb files. This script downloads to a temporary file
# and atomically renames (mv) it into place to avoid runtime memory corruption.
#

set -euo pipefail

# Target directory for GeoIP mmdb files (matches Anubis default or mounted docker volume)
GEOIP_DIR="${GEOIP_DIR:-${1:-/var/lib/GeoIP}}"
BASE_URL="https://raw.githubusercontent.com/P3TERX/GeoLite.mmdb/download"

# Databases required by Anubis
FILES=(
  "GeoLite2-ASN.mmdb"
  "GeoLite2-Country.mmdb"
  # Uncomment if City database is needed:
  # "GeoLite2-City.mmdb"
)

mkdir -p "${GEOIP_DIR}"

echo "[*] Updating GeoIP databases in ${GEOIP_DIR}..."

for file in "${FILES[@]}"; do
  url="${BASE_URL}/${file}"
  dest="${GEOIP_DIR}/${file}"
  tmp="${dest}.tmp.$$"

  echo "[-] Downloading ${file}..."
  if curl -fSL --retry 3 --connect-timeout 10 -o "${tmp}" "${url}"; then
    # Ensure downloaded file is non-empty before replacing
    if [ -s "${tmp}" ]; then
      # Atomic replace to protect memory-mapped Anubis reader
      mv -f "${tmp}" "${dest}"
      chmod 644 "${dest}"
      echo "[+] Successfully updated ${file} ($(du -h "${dest}" | cut -f1))"
    else
      echo "[!] Error: Downloaded file ${file} is empty. Skipping." >&2
      rm -f "${tmp}"
      exit 1
    fi
  else
    echo "[!] Failed to download ${file} from ${url}" >&2
    rm -f "${tmp}"
    exit 1
  fi
done

echo "[*] All GeoIP databases successfully updated!"
