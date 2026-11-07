#!/usr/bin/env bash
set -euo pipefail

# Convert Traditional Chinese docs (zh-TW/) to Simplified Chinese docs (zh-CN/)
# using OpenCC (tw2sp.json).

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC_DIR="$ROOT_DIR/zh-TW"
DEST_DIR="$ROOT_DIR/zh-CN"
CONFIG="${1:-tw2sp.json}"

if ! command -v opencc &> /dev/null; then
    echo "Error: opencc is not installed or not in PATH." >&2
    exit 1
fi

if [ ! -d "$SRC_DIR" ]; then
    echo "Error: Source directory $SRC_DIR not found." >&2
    exit 1
fi

echo "Converting $SRC_DIR -> $DEST_DIR using opencc ($CONFIG)..."
mkdir -p "$DEST_DIR"

count=0
while IFS= read -r -d '' src_file; do
    rel_path="${src_file#$SRC_DIR/}"
    dest_file="$DEST_DIR/$rel_path"
    mkdir -p "$(dirname "$dest_file")"
    opencc -i "$src_file" -o "$dest_file" -c "$CONFIG"
    count=$((count + 1))
done < <(find "$SRC_DIR" -type f -name "*.md" -print0)

echo "Successfully converted $count files to $DEST_DIR."
