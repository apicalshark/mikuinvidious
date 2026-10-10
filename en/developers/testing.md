# Testing

## Current state

`tests/` currently holds only regression tests (e.g. `test_reply_emoji.py`,
`test_review_regressions.py`); the `test` script in `package.json` is still a
placeholder (`echo "Error: no test specified"`). Python linting uses ruff:

```bash
npm run lint:python     # ruff check python
npm run format:python   # ruff format python
```

## Recommendations

- Add regression tests first: every fixed bug (see `test_review_regressions.py`
  for the pattern) should be locked in as a test so it never recurs.
- `dash_proxy.py` at 3000+ lines is the high-risk zone — write tests before
  touching mirror, watchdog, or signature logic.
- A pytest/CI harness doesn't exist yet. Building that foundation is a great
  first-PR topic.
