# 主題開發

模板放在 `templates/themes/`，`modern` 是正式支援的主題（Tailwind CSS、深淺色、mobile-first）。

## 改樣式

```bash
npm run build:css   # tailwind-input.css → main.css（minify）
```

靜態播放器（hls.js、mpegts.js、dash.js、danmaku.js 等）是內嵌（vendored）的：

```bash
npm run sync:static   # 從 npm 套件同步到 static/
```

## 排版檢查

```bash
npm run lint:frontend     # prettier check 加 djlint check
npm run format:frontend   # prettier write 加 djlint reformat
```

送 PR 前兩條都要過。`pyproject.toml` 的 `[tool.djlint]` 用的是 jinja profile。

## 幾個約定

- `base.html` 管頂端列（搜尋／番劇／主題／語言／偏好設定）和頁尾，`locale`、`dark_mode`、`locale_choices`、`asset_version`、`csp_nonce`、`i18n_catalog` 都是全域 context。
- 靜態資源網址掛 `?v={{ asset_version }}` 避開快取；發版有版本鍵值輪替，上游失效時提供 stale-serve。
- 錯誤頁走 `error.html`（狀態碼、描述、建議、回首頁），suggest 文案可以自己覆寫。
