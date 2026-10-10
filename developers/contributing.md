# Contributing

Contributions of any kind are welcome.

## Process

1. Get it running locally (see [Manual installation](../operators/manual-install))
   with `QUART_DEBUG=true`.
2. Run the full checks after changing code:

```bash
npm run lint     # ruff, prettier, djlint
npm run format   # format first if it fails
npm run check:i18n   # only needed when strings changed
```

3. Issues, PRs, and discussions are all fine. Before submitting, review
   `git status` / `git diff` and make sure no secrets are included.

## License

The app is GNU GPL-3.0. Frontend library licenses: see `/licenses` (hls.js and
mpegts.js are Apache-2.0; dash.js is BSD-3-Clause; media-chrome, Danmaku, and
opencc-js are MIT). Sync vendored files with `npm run sync:static` — don't copy
by hand.
