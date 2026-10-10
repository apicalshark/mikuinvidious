# Bilibili credentials

The system runs fine without credentials; anonymous access tops out at 1080P.
Credentials are only needed for higher qualities and paid / members-only content.

## How to obtain them

1. Log in to `bilibili.com` in a browser.
2. Open devtools → Application → Cookies and copy `SESSDATA`, `bili_jct`,
   `buvid3`, `buvid4`, `DedeUserID`.
3. `ac_time_value` is in the same page's Local Storage
   (`window.localStorage.ac_time_value`).

Step-by-step with screenshots:
[bilibili-api credential guide](https://nemo2011.github.io/bilibili-api/#/get-credential).

## How to configure

Fill in the `[credential]` section of `config.toml` and set `use_cred` to `true`.
For Docker, use the same-named environment variables (`SESSDATA`, `BILI_JCT`,
`BUVID3`, `BUVID4`, `DEDEUSERID`, `USE_CRED=true`).

## Encrypted storage (recommended)

Storing cookies in plaintext risks leaks. The project supports libsodium
(XChaCha20-Poly1305) encryption:

```bash
python tools/encrypt_secrets.py --generate-key   # master key, goes into SECRETS_MASTER_KEY
python tools/encrypt_secrets.py --encrypt SESSDATA "actual value"
```

Paste the resulting base64 string back into the config; it is decrypted automatically
at startup.

## Notes

- Credentials equal account access. **Never** commit them to a public repo or
  share them with anyone.
- Signs of expiry: high qualities stop working, paid episodes show only trailers.
  Re-obtain and replace.
- Enabling `SITE_SHOW_UNSAFE_ERROR_RESPONSE` on a public instance exposes error
  details to visitors. Turn it off when done debugging.
