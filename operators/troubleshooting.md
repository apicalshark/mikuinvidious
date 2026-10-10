# 故障排除

## 資料中心 IP 遭受風險控制（412／-352）

**症狀**：空間頁面顯示 0 投稿、搜尋結果為空、直播分區為空，但應用程式未記錄錯誤。

**成因**：Bilibili 對資料中心 IP 實施風險控制，屬於 IP 層級的阻擋，並非程式缺陷。

**處置**：於 `[proxy]` 設定 `proxy_url`（或 `HTTP_PROXY`），把連往 Bilibili 的流量經由 SOCKS5／HTTP 出口轉發（例如 Cloudflare WARP）。家用寬頻連線一般可直接連接，無需代理。

相關降級機制已內建於程式，無需手動處置：`User.get_user_info` 會退回非 WBI 的 `/x/web-interface/card`，`get_videos` 會退回 `recArchivesByKeywords`；降級期間頁面會顯示提示並回傳 `X-Degraded` 標頭。

## 搜尋結果一直是空的

請先排除上述風險控制因素。另一個常見成因：**不要把 `bili_ticket` Cookie 傳給 Web API**，該行為會觸發 `v_voucher` 預檢查，導致結果為空。本系統只在 CDN／DASH 代理使用 `x-bili-ticket` 標頭，遵循這個原則即可避免。

## 影片播放中斷

CDN 網址是時效簽名（有效期約兩小時）。簽名逾期的徵兆是整段內容拿不到，而不是播到一半斷線；播到一半斷線通常是邊緣節點連線品質問題。處置方式：重新整理以取得新簽名、切換畫質，或等待 mirror 容錯機制切換到健康節點。

## ERR_QUIC_PROTOCOL_ERROR

請在 Caddy 停用 HTTP/3，詳見[反向代理](reverse-proxy.md)。

## 直播間顯示未開播

請先確認上傳者實際開播狀態。若確認開播中，則可能是 `getInfoByRoom` 遭受風險控制——該端點需要 WBI 簽名與瀏覽器 TLS（本系統以 curl_cffi Chrome 偽裝實作），資料中心 IP 還是要走 WARP 轉發。

## 還是無法解決

啟用 `SITE_SHOW_UNSAFE_ERROR_RESPONSE=true` 查看完整錯誤（除錯完成後請關閉），並提供影片 ID／房間號與相關紀錄回報。
