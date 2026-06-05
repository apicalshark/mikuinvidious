# Copyright (C) 2023 MikuInvidious Team
#
# MikuInvidious is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License as
# published by the Free Software Foundation; either version 3 of
# the License, or (at your option) any later version.
#
# MikuInvidious is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with MikuInvidious. If not, see <http://www.gnu.org/licenses/>.

import os
import base64
from typing import Optional

try:
    import nacl.secret
    import nacl.utils
    import nacl.exceptions
    NACL_AVAILABLE = True
except ImportError:
    NACL_AVAILABLE = False


class SecretEncryption:
    """Encrypt/decrypt sensitive credentials using libsodium (XChaCha20-Poly1305)."""
    
    def __init__(self, master_key: Optional[bytes] = None):
        if not NACL_AVAILABLE:
            raise RuntimeError("PyNaCl not installed. Install with: pip install pynacl")
        
        if master_key is None:
            # Generate or load master key from environment
            key_b64 = os.environ.get("SECRETS_MASTER_KEY")
            if key_b64:
                master_key = base64.b64decode(key_b64)
            else:
                # Generate a new key (will be lost on restart - for ephemeral use only)
                master_key = nacl.utils.random(nacl.secret.SecretBox.KEY_SIZE)
        
        if len(master_key) != nacl.secret.SecretBox.KEY_SIZE:
            raise ValueError(f"Master key must be {nacl.secret.SecretBox.KEY_SIZE} bytes")
        
        self._box = nacl.secret.SecretBox(master_key)
    
    def encrypt(self, plaintext: str) -> str:
        """Encrypt a string and return base64-encoded ciphertext."""
        if not plaintext:
            return ""
        nonce = nacl.utils.random(nacl.secret.SecretBox.NONCE_SIZE)
        ciphertext = self._box.encrypt(plaintext.encode(), nonce)
        # Prepend nonce to ciphertext for storage
        return base64.b64encode(nonce + ciphertext).decode()
    
    def decrypt(self, ciphertext_b64: str) -> str:
        """Decrypt a base64-encoded ciphertext and return plaintext."""
        if not ciphertext_b64:
            return ""
        try:
            data = base64.b64decode(ciphertext_b64)
            nonce = data[:nacl.secret.SecretBox.NONCE_SIZE]
            ciphertext = data[nacl.secret.SecretBox.NONCE_SIZE:]
            plaintext = self._box.decrypt(ciphertext, nonce)
            return plaintext.decode()
        except (nacl.exceptions.CryptoError, ValueError) as e:
            raise ValueError(f"Decryption failed: {e}")
    
    @staticmethod
    def generate_master_key() -> str:
        """Generate a new master key and return as base64 string."""
        if not NACL_AVAILABLE:
            raise RuntimeError("PyNaCl not installed")
        key = nacl.utils.random(nacl.secret.SecretBox.KEY_SIZE)
        return base64.b64encode(key).decode()


# Global instance (initialized on first use)
_encryption_instance: Optional[SecretEncryption] = None


def get_encryption() -> SecretEncryption:
    """Get or create the global encryption instance."""
    global _encryption_instance
    if _encryption_instance is None:
        _encryption_instance = SecretEncryption()
    return _encryption_instance


def encrypt_secret(plaintext: str) -> str:
    """Convenience function to encrypt a secret."""
    return get_encryption().encrypt(plaintext)


def decrypt_secret(ciphertext_b64: str) -> str:
    """Convenience function to decrypt a secret."""
    return get_encryption().decrypt(ciphertext_b64)