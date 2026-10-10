# 參與貢獻

任何形式的貢獻都歡迎。

## 流程

1. 先在本地跑起來（見[手動安裝](../operators/manual-install.md)），開 `QUART_DEBUG=true`。
2. 改完跑完整檢查：

```bash
npm run lint     # ruff、prettier、djlint
npm run format   # 沒過就先排好
npm run check:i18n   # 有動到字串才需要
```

3. 開 issue、PR 或 discussion 都可以；提交前看一下 `git status`、`git diff`，確認沒有把機密資訊送上去。

## 授權

主程式用 GNU GPL-3.0；前端函式庫授權見 `/licenses`（hls.js、mpegts.js 是 Apache-2.0；dash.js 是 BSD-3-Clause；media-chrome、Danmaku、opencc-js 是 MIT）。內嵌檔案用 `npm run sync:static` 同步，不要手動複製。
