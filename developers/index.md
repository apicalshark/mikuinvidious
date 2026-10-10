# 開發者指南 — 總覽

技術棧：Python 3.14 以上版本、Quart（ASGI）、Granian（Rust 實作的 ASGI 伺服器）、Redis、Jinja2、Tailwind CSS。Bilibili API 是自有的 `python/api/` 封裝（2026 年 9 月從已封存的 `bilibili-api-python` 遷移完成）。

## 程式碼結構

| 檔案 | 職責 |
| :--- | :--- |
| `python/app.py` | 建立 Quart 應用、註冊 blueprint、錯誤處理 |
| `python/views.py` | 主要路由：首頁、搜尋、影片、空間、直播、音訊 |
| `python/views_bangumi.py` | 番劇 blueprint 與 Nyaa 搜尋 |
| `python/shared.py` | 組態、`httpx` 客戶端、Redis 連接、主題工具 |
| `python/proxy.py` | Progressive 影音與圖片代理（`CdnConnection` raw socket） |
| `python/dash_proxy.py` | DASH 完整鏈路：playurl 取得、MPD 產生、軌道代理、合併下載 |
| `python/live_manager.py` | 直播長連接管理、區塊緩衝、心跳注入 |
| `python/api/` | Bilibili API 封裝（WBI 簽名、ticket、重試機制） |
| `templates/themes/`、`static/` | 主題（`modern` 是正式支援的）與內嵌播放器 |

建議閱讀順序：[系統架構](architecture.md) → [播放與下載架構](playback-stack.md) → [路由參照](api-reference.md) → [API 封裝](bilibili-api.md)。介面相關請參閱[主題](theming.md)、[多國語系](i18n.md)。
