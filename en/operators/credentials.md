# Bilibili credentials

Optional. Anonymous access caps at 1080P. Credentials unlock higher qualities and
paid / members-only content.

## Obtaining them

1. Log in to `bilibili.com`.
2. Devtools → Application → Cookies: copy `SESSDATA`, `bili_jct`, `buvid3`,
   `buvid4`, `DedeUserID`.
3. `ac_time_value` from the same page's Local Storage
   (`window.localStorage.ac_time_value`).

Screenshots: [bilibili-api credential guide](https://nemo2011.github.io/bilibili-api/#/get-credential).

## Configuring

Fill `[credential]` in `config.toml`, set `use_cred = true`. Docker: same-named
env vars (`SESSDATA`, `BILI_JCT`, `BUVID3`, `BUVID4`, `DEDEUSERID`, `USE_CRED=true`).

## Encrypted storage

Plaintext cookies leak. The project supports libsodium (XChaCha20-Poly1305):

```bash
python tools/encrypt_secrets.py --generate-key   # master key -> SECRETS_MASTER_KEY
python tools/encrypt_secrets.py --encrypt SESSDATA "value"
```

Paste the base64 output back into the config. Decrypted automatically at startup.

## Notes

- Credentials are account access. Never commit them to a public repo.
- Expiry signs: high qualities stop working, paid episodes show trailers only.
  Re-obtain and replace.
- `SITE_SHOW_UNSAFE_ERROR_RESPONSE=true` exposes error details to visitors.
  Turn it off after debugging.
