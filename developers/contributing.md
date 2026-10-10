# Contributing

Any contribution welcome.

1. Run locally ([Manual installation](../operators/manual-install)),
   `QUART_DEBUG=true`.
2. After changing code:

```bash
npm run lint     # ruff, prettier, djlint
npm run format   # format first on failure
npm run check:i18n   # strings changed only
```

3. Issues, PRs, discussions all fine. Check `git status` / `git diff` before
   submitting — no secrets.

License: app is GNU GPL-3.0. Frontend licenses: `/licenses` (hls.js, mpegts.js:
Apache-2.0; dash.js: BSD-3-Clause; media-chrome, Danmaku, opencc-js: MIT).
Vendored files sync via `npm run sync:static`, never by hand.
