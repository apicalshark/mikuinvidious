## 2024-05-24 - Credentials saved as plaintext in config.toml
**Vulnerability:** The python/refresher.py script was saving updated cookies, including session secrets in plain text inside config.toml file via [updatedcred] configuration block, writing these on the file system in clear text.
**Learning:** This implies a failure to implement credentials security during the update cycle, storing secrets without encryption inside a user configurable configuration file.
**Prevention:** Implement the SecretEncryption mechanism from `python/secrets_encryption.py` to encrypt cookies prior to writing them to config.toml and decrypt them during loading phase, using `encrypt_secret` and a `decrypt_if_encrypted` wrapper method.
