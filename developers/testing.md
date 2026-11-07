# Testing & Verification Guidelines

To ensure rapid iteration without regressing critical streaming and proxy invariants, MikuInvidious enforces a strict verification decision matrix and test suite.

---

## 1. Verification Decision Matrix (MANDATORY)

Do not run tests indiscriminately. Evaluate the scope of your changes first:

| Modification Scope | Required Actions | Strictly FORBIDDEN / Redundant Actions |
| :--- | :--- | :--- |
| **Documentation / Markdown only** (`*.md`, `doc/*`) | Check git status & verify markdown link targets (`npm run build` in docs repo) | **DO NOT run unit tests, compileall, or linters.** |
| **Frontend assets only** (`static/*`, `templates/*`) | `npm run lint:frontend`<br>`npm run build:css` (if Tailwind CSS modified)<br>`npm run build:i18n` (if translatable strings change)<br>`npm run check:i18n` (to verify catalogs) | **DO NOT run Python unit tests.** |
| **Python code modified** (`python/*`, `tests/*`) | `uv run ruff check python`<br>`uv run python -m compileall -q python tests tools`<br>`npm run build:i18n` (if strings change)<br>`npm run check:i18n` (if catalogs change)<br>`NO_PROXY="localhost,127.0.0.1,::1" no_proxy="localhost,127.0.0.1,::1" PYTHONPATH=python uv run python -m unittest discover tests` | Do not skip bytecode or ruff checks before PRs. |

---

## 2. Command Reference

### Python Test Suite

Run only when `python/*` or `tests/*` change:

```bash
NO_PROXY="localhost,127.0.0.1,::1" no_proxy="localhost,127.0.0.1,::1" PYTHONPATH=python uv run python -m unittest discover tests
```

### Bytecode Compilation Check

Checks for syntax errors across all application modules and scripts without executing imports:

```bash
uv run python -m compileall -f python tests tools
```

### Python Linting & Formatting

```bash
uv run ruff check python
uv run ruff format python
```

### Frontend Linting & Formatting

When static JS/CSS or Jinja templates change:

```bash
npm run lint:frontend
npm run format:frontend
```

### CSS & Static Asset Rebuild

```bash
npm run build:css     # after editing static/themes/modern/css/tailwind-input.css
npm run sync:static   # after bumping packages in package.json
```

### i18n Translation Check & Build

```bash
npm run check:i18n
npm run build:i18n
```

---

## 3. High-Risk Testing Zones

- **DASH Engine (`python/dash_*.py`)**: Any modifications to mirror failover, bounded Range checks, keep-alive socket pools, or signature refresh MUST be validated against `tests/test_mpd.py` and `tests/test_proxy_flood.py`.
- **Live Stream Manager (`python/live_manager.py`)**: Validated via `tests/test_live.py`.
- **Danmaku Converter (`python/danmaku.py`)**: Validated via `tests/test_danmaku.py`.
- **Image Disk Cache (`python/image_cache.py`)**: Validated via `tests/test_image_cache.py`.
