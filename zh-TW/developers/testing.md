# 測試與驗證指南

為了在快速迭代的同時確保不破壞關鍵的串流與代理不變原則，MikuInvidious 實施嚴格的驗證決策矩陣與測試流程。

---

## 1. 驗證決策矩陣（強制執行）

嚴禁不加思考地盲目執行全套測試。修改前請先評估修改範疇：

| 修改範疇 | 必要執行動作 | 嚴格禁止 ／ 多餘動作 |
| :--- | :--- | :--- |
| **僅修改文件與 Markdown**（`*.md`, `doc/*`） | 檢查 git status 與確認檔案連結（於文件庫執行 `npm run build`） | **絕不執行單元測試、compileall 或程式碼風格檢查。** |
| **僅修改前端靜態資源**（`static/*`, `templates/*`） | `npm run lint:frontend`<br>`npm run build:css`（若修改了 Tailwind CSS）<br>`npm run build:i18n`（若修改了需翻譯字串）<br>`npm run check:i18n`（驗證字典完整度） | **絕不執行 Python 單元測試。** |
| **修改 Python 程式碼**（`python/*`, `tests/*`） | `uv run ruff check python`<br>`uv run python -m compileall -q python tests tools`<br>`npm run build:i18n`（若字串變更）<br>`npm run check:i18n`（若字典變更）<br>`NO_PROXY="localhost,127.0.0.1,::1" no_proxy="localhost,127.0.0.1,::1" PYTHONPATH=python uv run python -m unittest discover tests` | 發起 PR 前嚴禁跳過語法編譯檢查或 ruff 檢查。 |

---

## 2. 指令參考手冊

### Python 單元測試集

僅在 `python/*` 或 `tests/*` 異動時執行：

```bash
NO_PROXY="localhost,127.0.0.1,::1" no_proxy="localhost,127.0.0.1,::1" PYTHONPATH=python uv run python -m unittest discover tests
```

### 位元組碼語法編譯驗證

檢查所有程式碼與工具腳本之 Python 語法錯誤（不載入執行模組）：

```bash
uv run python -m compileall -f python tests tools
```

### Python 程式碼風格與格式化

```bash
uv run ruff check python
uv run ruff format python
```

### 前端代碼風格與格式化

當靜態 JS/CSS 或 Jinja 範本異動時：

```bash
npm run lint:frontend
npm run format:frontend
```

### CSS 與靜態資源重新編譯

```bash
npm run build:css     # 編輯 static/themes/modern/css/tailwind-input.css 之後
npm run sync:static   # 更新 package.json 相依性套件版本之後
```

### 多國語系字典檢查與編譯

```bash
npm run check:i18n
npm run build:i18n
```

---

## 3. 高風險測試核心區域

- **DASH 串流引擎（`python/dash_*.py`）**：任何針對鏡像容錯、有界 Range 檢查、長連線複用池或簽章重整之修改，必須通過 `tests/test_mpd.py` 與 `tests/test_proxy_flood.py` 驗證。
- **直播串流管理（`python/miku/media/live_manager.py`）**：由 `tests/test_live.py` 覆蓋驗證。
- **即時彈幕轉換（`python/miku/media/danmaku.py`）**：由 `tests/test_danmaku.py` 覆蓋驗證。
- **圖片磁碟快取（`python/miku/media/image_cache.py`）**：由 `tests/test_image_cache.py` 覆蓋驗證。
