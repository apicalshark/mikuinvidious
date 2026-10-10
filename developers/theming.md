# Theming

Templates: `templates/themes/`. `modern` is the supported theme (Tailwind,
dark/light, mobile-first).

Styles:

```bash
npm run build:css   # tailwind-input.css → main.css (minified)
```

Static players (hls.js, mpegts.js, dash.js, danmaku.js, …) are vendored:

```bash
npm run sync:static   # npm packages → static/
```

Lint:

```bash
npm run lint:frontend     # prettier check + djlint check
npm run format:frontend   # prettier write + djlint reformat
```

Both must pass before a PR. `[tool.djlint]` uses the jinja profile.

Conventions:

- `base.html` owns header (search / bangumi / theme / language / preferences) and
  footer. `locale`, `dark_mode`, `locale_choices`, `asset_version`, `csp_nonce`,
  `i18n_catalog` are global context.
- Static URLs carry `?v={{ asset_version }}`. Releases rotate version keys;
  stale served on upstream failure.
- Errors use `error.html` (status, description, suggestion, home link). Suggest
  copy is overridable.
