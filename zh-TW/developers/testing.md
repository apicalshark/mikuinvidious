# 測試

## 現況

`tests/` 目前只有迴歸測試（例如 `test_reply_emoji.py`、`test_review_regressions.py`），`package.json` 的 `test` 還是佔位指令（`echo "Error: no test specified"`）。Python 靜態檢查用 ruff：

```bash
npm run lint:python     # ruff check python
npm run format:python   # ruff format python
```

## 寫測試的建議

- 先補迴歸測試：每次修掉的 bug（參照 `test_review_regressions.py` 的寫法）都要鎖成測試，避免重犯。
- 超過 3000 行的 `dash_proxy.py` 是高風險區，動 mirror、watchdog 或簽名邏輯之前先寫測試。
- pytest／CI harness 還沒建——補上這個基礎建設是很好的第一個 PR 題目。
