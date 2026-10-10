# Theming

Templates live in `templates/themes/`; `modern` is the officially supported theme
(Tailwind CSS, dark/light, mobile-first).

## Changing styles

```bash
npm run build:css   # tailwind-input.css → main.css (minified)
```

Static players (hls.js, mpegts.js, dash.js, danmaku.js, …) are vendored:

```bash
npm run sync:static   # sync from npm packages into static/
```

## Lint

```bash
npm run lint:frontend     # prettier check + djlint check
npm run format:frontend   # prettier write + djlint reformat
```

Both must pass before a PR. `[tool.djlint]` in `pyproject.toml` uses the jinja profile.

## Conventions

- `base.html` owns the header (search / bangumi / theme / language / preferences)
  and footer. `locale`, `dark_mode`, `locale_choices`, `asset_version`, `csp_nonce`,
  and `i18n_catalog` are global context.
- Static asset URLs carry `?v={{ asset_version }}` for cache busting. Releases rotate
  version keys and serve stale on upstream failure.
- Error pages use `error.html` (status code, description, suggestion, back home).
  The suggest copy can be overridden.
