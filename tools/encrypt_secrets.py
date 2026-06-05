#!/usr/bin/env python3
"""
Utility to encrypt Bilibili credentials for MikuInvidious.

Usage:
    python tools/encrypt_secrets.py --generate-key
    python tools/encrypt_secrets.py --encrypt SESSDATA "your_sessdata_value"
    python tools/encrypt_secrets.py --encrypt-all
"""

import argparse
import os
import sys

# Add python directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

from secrets_encryption import SecretEncryption, NACL_AVAILABLE


def main():
    if not NACL_AVAILABLE:
        print("Error: PyNaCl not installed. Install with: pip install pynacl")
        sys.exit(1)
    
    parser = argparse.ArgumentParser(description="Encrypt secrets for MikuInvidious")
    parser.add_argument("--generate-key", action="store_true", help="Generate a new master key")
    parser.add_argument("--encrypt", nargs=2, metavar=("NAME", "VALUE"), help="Encrypt a single value")
    parser.add_argument("--encrypt-all", action="store_true", help="Interactively encrypt all credentials")
    
    args = parser.parse_args()
    
    if args.generate_key:
        key = SecretEncryption.generate_master_key()
        print(f"SECRETS_MASTER_KEY={key}")
        print("\nAdd this to your environment or .env file:")
        print(f"export SECRETS_MASTER_KEY={key}")
        return
    
    # Load or generate master key
    master_key_b64 = os.environ.get("SECRETS_MASTER_KEY")
    if master_key_b64:
        import base64
        master_key = base64.b64decode(master_key_b64)
        encryption = SecretEncryption(master_key)
        print("Using existing SECRETS_MASTER_KEY from environment")
    else:
        encryption = SecretEncryption()
        print("WARNING: No SECRETS_MASTER_KEY set. Using ephemeral key (will not work across restarts!)")
        print("Run with --generate-key to create a persistent key.")
    
    if args.encrypt:
        name, value = args.encrypt
        encrypted = encryption.encrypt(value)
        print(f"{name}={encrypted}")
        return
    
    if args.encrypt_all:
        print("Enter credentials to encrypt (press Enter to skip):")
        fields = [
            "SESSDATA",
            "BILI_JCT",
            "BUVID3",
            "BUVID4",
            "DEDEUSERID",
            "AC_TIME_VALUE",
        ]
        for field in fields:
            value = input(f"{field}: ").strip()
            if value:
                encrypted = encryption.encrypt(value)
                print(f"{field}={encrypted}")
        return
    
    parser.print_help()


if __name__ == "__main__":
    main()