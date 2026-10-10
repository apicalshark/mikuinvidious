# 日常維運

## 查閱紀錄

```bash
docker compose logs -f app      # 主程式
docker compose logs -f caddy    # 反向代理
```

Granian 存取紀錄的輸出量較大（每段影音、每張圖片、每次輪詢都會產生一行），由 `SERVER_ACCESS_LOG` 控制，預設關閉，建議保持關閉。

## 更新

```bash
git pull
docker compose up -d --build
```

手動部署：`git pull` 後執行 `uv sync`，若前端有變更，另外執行 `npm run build:css`。

## 備份範圍

- `config.toml`（或 Compose 的環境變數）：站點組態的核心。
- Redis：遺失時只需要重建快取與工作階段，無需備份。但若未設定固定的 `QUART_SECRET_KEY`，重新啟動本來就會讓工作階段失效，正式站點請務必設定。
- 憑證：採用加密存放時，`SECRETS_MASTER_KEY` 應與組態檔分開保管。
