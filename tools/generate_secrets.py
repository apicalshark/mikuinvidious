#!/usr/bin/env python3
# Copyright (c) 2023-2026 MikuInvidious Team
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Generate deployment secrets for MikuInvidious (.env / compose.yml).

Usage:
    python3 tools/generate_secrets.py
    python3 tools/generate_secrets.py --include-master-key
    python3 tools/generate_secrets.py --write .env
    python3 tools/generate_secrets.py --write .env --force
"""

from __future__ import annotations

import argparse
import base64
import os
import secrets
from pathlib import Path


def generate_secrets(include_master_key: bool = False) -> dict[str, str]:
    values = {
        "QUART_SECRET_KEY": secrets.token_hex(32),
        "REDIS_PASSWORD": secrets.token_hex(24),
    }
    if include_master_key:
        values["SECRETS_MASTER_KEY"] = base64.b64encode(secrets.token_bytes(32)).decode("ascii")
    return values


def write_env_file(path: Path, values: dict[str, str], force: bool = False) -> tuple[list[str], list[str]]:
    existing_lines: list[str] = []
    existing_keys: dict[str, int] = {}
    if path.exists():
        existing_lines = path.read_text(encoding="utf-8").splitlines()
        for idx, line in enumerate(existing_lines):
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, _, val = stripped.partition("=")
            key = key.strip()
            if key in values:
                existing_keys[key] = idx if val.strip() else -idx - 1

    written: list[str] = []
    skipped: list[str] = []
    output_lines = list(existing_lines)

    for key, secret in values.items():
        entry = f"{key}={secret}"
        if key in existing_keys:
            idx = existing_keys[key]
            if idx >= 0 and not force:
                skipped.append(key)
                continue
            target_idx = idx if idx >= 0 else -idx - 1
            output_lines[target_idx] = entry
        else:
            output_lines.append(entry)
        written.append(key)

    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    fd_owned = True
    try:
        os.chmod(fd, 0o600)
        handle = os.fdopen(fd, "w", encoding="utf-8")
        fd_owned = False
        with handle:
            handle.write("\n".join(output_lines) + "\n")
    except BaseException:
        if fd_owned:
            os.close(fd)
        raise

    return written, skipped


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate cryptographic secrets for MikuInvidious deployment.")
    parser.add_argument(
        "--include-master-key",
        action="store_true",
        help="Also generate SECRETS_MASTER_KEY for encrypted Bilibili credentials",
    )
    parser.add_argument(
        "--write",
        nargs="?",
        const=".env",
        default=None,
        metavar="PATH",
        help="Write generated secrets to an env file with 0600 permissions (default: .env)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing non-empty keys when used with --write",
    )
    args = parser.parse_args()

    values = generate_secrets(include_master_key=args.include_master_key)
    if args.write is None:
        print("Generated deployment secrets. Use --write [PATH] to store them in an env file with 0600 permissions.")
        return

    target = Path(args.write)
    written, skipped = write_env_file(target, values, force=args.force)
    if written:
        print(f"Written secrets to {target} (mode 0600).")
    if skipped:
        print(f"Preserved {len(skipped)} existing key(s) (use --force to rotate).")


if __name__ == "__main__":
    main()
