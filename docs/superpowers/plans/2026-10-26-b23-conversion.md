# B23.tv Shortlink Conversion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Provide comprehensive b23.tv shortlink resolution and de-tracking across MikuInvidious: search box auto-jump, root `/<b32tvid>` route support for all media types, a dedicated `/convert` tool and `/api/convert` endpoint, and a footer entry.

**Architecture:** A centralized `python/b23_resolver.py` module extracts and safely resolves b23 shortlinks, strips Bilibili tracking parameters, and maps destinations to both clean Bilibili URLs and local MikuInvidious paths. This module is consumed by the search view, the root shortlink redirect route, and the new `/convert` + `/api/convert` endpoints.

**Tech Stack:** Python 3.15, Quart (async), Jinja2 templates, Tailwind CSS (modern theme), pytest.

**Spec:** Option D (Search box auto-jump + root route extension for all types + dedicated `/convert` page and API + footer link) agreed in conversation.

## Global Constraints

- **Python Floor:** Requires Python 3.15+ (async / Quart conventions).
- **Security (SSRF):** Resolver must only connect to `b23.tv` (or strictly validated `bilibili.com` domains) and follow exactly one 302 hop; never arbitrary domains or internal IPs.
- **De-tracking:** Strip all analytics/tracking query params (`spm_id_from`, `buvid`, `from_source`, `from_spmid`, `share_source`, `share_medium`, `share_plat`, `share_tag`, `share_session_id`, `bbid`, `ts`, `unique_k`, etc.).
- **Theme Consistency:** UI must use existing Tailwind tokens (`bg-surface`, `text-on-surface`, `border-outline`, `hover:text-primary`, etc.) matching `templates/themes/modern/`.
- **Localization:** New user-visible strings must be wrapped in `_('...')` with translation entries added.

## Review Focus

1. **Dirty app share text:** App share text containing emojis, titles, newlines, and trailing text (`【视频标题】https://b23.tv/xyz123 赶紧来看`) must cleanly extract the shortlink without crashing.
2. **SSRF and malformed domains:** Inputs like `https://attacker.com/b23.tv/xyz` or non-b23 domains must be rejected safely without making external network calls.
3. **Bangumi ep vs ss routing:** Redirect target `/bangumi/play/ep12345` must map to `/bangumi/play/ep12345`, and `/bangumi/play/ss12345` must map to `/bangumi/play/ss12345` or `/bangumi/view/12345`.
4. **No-JS fallback:** `/convert` must function with standard HTML `<form method="POST">` when JavaScript is disabled, while supporting asynchronous fetch when JavaScript is active.
5. **Search fallback:** When a search query contains no b23 link or fails to resolve, search must continue as normal without 500 error.

---

### Task 1: Core B23 Resolver Module

**Files:**
- Create: `python/b23_resolver.py`
- Test: `tests/test_b23_resolver.py`

**Interfaces:**
- Produces:
  - `extract_b23_or_bili_url(text: str) -> Optional[str]`
  - `clean_bilibili_url(target_url: str) -> dict`
    - Returns `{ "clean_url": str, "local_path": str, "type": str, "id": str }`
  - `async resolve_b23_target(input_text: str, client=None) -> dict`
    - Returns `{ "status": "ok"|"error", "original": str, "target_url": str, "clean_url": str, "local_path": str, "type": str, "id": str, "error": Optional[str] }`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_b23_resolver.py` with tests covering:
- Regex extraction of `b23.tv/<code-or-id>` from plain URLs and app share text.
- SSRF rejection: ignore/reject domains that are not strictly `b23.tv` or `*.b23.tv`.
- Query parameter de-tracking: strip `spm_id_from`, `buvid`, `share_medium`, etc.
- Target classification and local path mapping:
  - Video (`/video/BV...` / `/video/av...`)
  - Bangumi ep (`/bangumi/play/ep...`)
  - Bangumi ss (`/bangumi/play/ss...`)
  - Read/Opus (`/read/cv...`, `/read/opus...`)
  - Live (`/live/...`)
  - Space (`/space/...`)
  - Audio (`/audio/au...`, `/audio/am...`)
- Async resolution with mocked `httpx` client returning 302 Location.

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=python uv run --with pytest pytest tests/test_b23_resolver.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'b23_resolver'`

- [ ] **Step 3: Implement `b23_resolver.py`**

Implement:
- `extract_b23_or_bili_url(text: str)`
- `clean_bilibili_url(target_url: str)`
- `async resolve_b23_target(input_text: str, client=None)` using `shared.Network.get_async_client()` if client not provided, with timeout and `follow_redirects=False`.

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=python uv run --with pytest pytest tests/test_b23_resolver.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add python/b23_resolver.py tests/test_b23_resolver.py
git commit -m "feat(b23): add core resolver and de-tracking module"
```

---

### Task 2: Search Auto-Jump & Root Route `/<b32tvid>` Refactor

**Files:**
- Modify: `python/views.py:226-260` (`search_view`)
- Modify: `python/app.py:212-276` (`b32tv_redirect`)
- Test: `tests/test_b23_routes.py`

**Interfaces:**
- Consumes: `b23_resolver.resolve_b23_target`, `b23_resolver.extract_b23_or_bili_url`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_b23_routes.py` testing:
- `GET /search?q=https://b23.tv/abc123` redirects to the resolved `local_path` (e.g. `/video/BV1xx411c7xx` or `/bangumi/play/ep123`).
- `GET /search?q=normal_keyword` continues to search without redirecting.
- `GET /<b32tvid>` uses `resolve_b23_target` and supports bangumi, live, opus, and video redirects.

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=python uv run --with pytest pytest tests/test_b23_routes.py -v`
Expected: FAIL

- [ ] **Step 3: Update `search_view` in `python/views.py` and `b32tv_redirect` in `python/app.py`**

- In `search_view`:
  - Check if `extract_b23_or_bili_url(q)` finds a b23 link.
  - If so, call `await resolve_b23_target(q)`.
  - If resolved with a `local_path`, `return redirect(res["local_path"])`.
  - Otherwise fall through gracefully to keyword search.
- In `b32tv_redirect`:
  - Delegate to `await resolve_b23_target(f"https://b23.tv/{b32tvid}")`.
  - Redirect to `res["local_path"]`. If resolution fails, render error page.

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=python uv run --with pytest pytest tests/test_b23_routes.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add python/views.py python/app.py tests/test_b23_routes.py
git commit -m "feat(b23): integrate b23 resolution into search and root redirect routes"
```

---

### Task 3: Dedicated `/convert` Page and `/api/convert` Endpoint

**Files:**
- Modify: `python/views.py` (add `convert_view` and `api_convert`)
- Create: `templates/themes/modern/convert.html`
- Test: `tests/test_convert_view.py`

**Interfaces:**
- Consumes: `b23_resolver.resolve_b23_target`
- Produces:
  - Route `GET /convert`: Render converter form
  - Route `POST /convert`: Form submission rendering converter with results
  - Route `POST /api/convert`: JSON endpoint returning `{ "status": ..., "clean_url": ..., "local_path": ..., "type": ..., "id": ... }`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_convert_view.py` testing:
- `GET /convert` returns 200 with HTML form.
- `POST /convert` with `url` form param returns 200 with resolved data.
- `POST /api/convert` with JSON `{"url": "https://b23.tv/..."}` returns 200 JSON.
- `POST /api/convert` with invalid URL returns 400 JSON.

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=python uv run --with pytest pytest tests/test_convert_view.py -v`
Expected: FAIL

- [ ] **Step 3: Implement routes and template**

- Add `convert_view` (`GET`, `POST`) and `api_convert` (`POST`) to `python/views.py` with rate limiting.
- Create `templates/themes/modern/convert.html` extending `base.html`:
  - Clean card container.
  - Textarea/input supporting pasted link or share text.
  - Convert button with loading state.
  - Result area displaying:
    - Clean Bilibili URL with Copy button.
    - MikuInvidious link with "Open" and Copy button.
    - Media type badge.
  - Interactive JavaScript for AJAX conversion + copy-to-clipboard, with graceful fallback to standard form POST.

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=python uv run --with pytest pytest tests/test_convert_view.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add python/views.py templates/themes/modern/convert.html tests/test_convert_view.py
git commit -m "feat(convert): add /convert page and /api/convert endpoint"
```

---

### Task 4: Footer Integration & Translations

**Files:**
- Modify: `templates/themes/modern/base.html:454-500`
- Modify: `locales/zh_CN/LC_MESSAGES/messages.po`
- Modify: `locales/zh_TW/LC_MESSAGES/messages.po`
- Modify: `locales/en/LC_MESSAGES/messages.po`
- Test: `tests/test_convert_view.py` (add footer assertion)

**Interfaces:**
- Consumes: `/convert` URL

- [ ] **Step 1: Write the failing test**

In `tests/test_convert_view.py`, assert that `/` response HTML contains an `href="/convert"` link in the footer.

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=python uv run --with pytest pytest tests/test_convert_view.py -k test_footer -v`
Expected: FAIL

- [ ] **Step 3: Add footer link and translation keys**

- In `templates/themes/modern/base.html`, add `<a href="/convert" class="hover:text-primary transition-colors font-medium">{{ _('Link converter') }}</a>` under the first/second column.
- Update `messages.po` files for `en`, `zh_CN`, and `zh_TW` with translations for `Link converter`, `Convert Bilibili / B23 Shortlink`, etc.
- Compile po files to mo if necessary (`pybabel compile` or equivalent if used in repo, or verify gettext setup).

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=python uv run --with pytest pytest tests/test_convert_view.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add templates/themes/modern/base.html locales/ tests/test_convert_view.py
git commit -m "feat(footer): add link converter entry to footer with translations"
```

---

### Task 5: Full Regression and Integration Verification

**Files:**
- Test: All tests in `tests/`

- [ ] **Step 1: Run all test suites**

Run: `PYTHONPATH=python uv run --with pytest pytest tests/ -v`
Expected: All tests pass without regressions.

- [ ] **Step 2: Commit any final polish**

```bash
git commit -m "chore: complete b23 conversion integration verification"
```
