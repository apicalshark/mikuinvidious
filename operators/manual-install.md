# 手動安裝

本章適用於不使用 Docker、需直接修改程式碼的開發者。系統需求為 Python 3.14 以上版本、Redis，以及 `uv` 套件管理工具。

## 系統依賴（Debian／Ubuntu）

```bash
sudo apt update
sudo apt install python3 python3-venv git curl
curl -LsSf https://astral.sh/uv/install.sh | sh
```

另需安裝 [Redis](https://redis.io/docs/latest/operate/oss_and_stack/install/archive/install-redis/install-redis-on-linux/)（預設監聽 6379 即可）與 [Caddy](https://caddyserver.com/docs/install）。如需修改前端樣式，另需 Node.js 18 以上版本。

## 安裝步驟

```bash
git clone https://github.com/apicalshark/mikuinvidious
cd mikuinvidious
uv sync
npm install
npm run build:css        # 編譯 Tailwind（僅於修改 CSS 後需要）
cp config.toml.sample config.toml
```

編輯 `config.toml`：於 `[server]` 設定 secret（未設定時將於每次啟動自動產生，但重新啟動後工作階段將失效）；於 `[redis]` 確認 `url = "redis://localhost:6379"`；若主機位於資料中心，請於 `[proxy]` 設定 `proxy_url`。

```bash
uv run python/main.py
```

應用程式預設監聽 `http://localhost:8888`，經由 Caddy 轉發後為 8000：

```text
:8000 {
    handle /static/* {
        root * ./static
        file_server
    }
    reverse_proxy localhost:8888
}
```

開發期間建議啟用除錯模式以取得自動重新載入：`QUART_DEBUG=true uv run python/main.py`。

## 提交前檢查

```bash
npm run lint      # 執行 ruff、prettier、djlint 完整檢查
npm run format    # 自動格式化後再送出 PR
```
