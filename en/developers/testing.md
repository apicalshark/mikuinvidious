# Testing

`tests/` holds regression tests only (`test_reply_emoji.py`,
`test_review_regressions.py`). `test` in `package.json` is still a placeholder.
Python lint is ruff:

```bash
npm run lint:python     # ruff check python
npm run format:python   # ruff format python
```

- Add regression tests first: every fixed bug (see `test_review_regressions.py`)
  gets locked in as a test.
- `dash_proxy.py` (3000+ lines) is the high-risk zone. Tests before touching
  mirror, watchdog, or signature logic.
- No pytest/CI harness yet. Building it is a good first PR.
