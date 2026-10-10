# MikuInvidious 概述

MikuInvidious 是一套自由開源的 Bilibili 前端服務，其設計理念源自 [Invidious](https://invidious.io)。本系統提供輕量化且注重隱私的介面，用於瀏覽與觀看 Bilibili 內容——無需註冊帳號，無需安裝官方應用程式，亦不進行任何追蹤。

## 功能範圍

- **影片觀看**：支援 DASH 自適應串流、彈幕疊加、字幕及多分集（多 P）影片。
- **純音訊模式**：任何影片均可改為僅播放音訊，適用於音樂聆聽、Podcast，或節省網路流量的場景。
- **直播**：具備穩定的轉發代理、心跳維持機制與即時聊天室。
- **番劇**：提供番劇與劇集的瀏覽及選集功能，並可選擇性整合 Nyaa.si 種子搜尋。
- **專欄與動態**：Bilibili 的 `cv` 專欄與 `opus` 動態將轉換為簡潔的代理頁面。
- **全站搜尋**：支援影片、上傳者（UP 主）、專欄、直播間及番劇，並提供篩選條件。
- **隱私預設**：所有影音內容均經由伺服器端代理傳輸（客戶端 IP 不會直接暴露於 Bilibili CDN），免帳號、零追蹤。

## 本書導讀

| 章節 | 適用對象 |
| :--- | :--- |
| [站長指南](operators/) | 部署與管理實例的人員（Docker、Caddy、Redis、憑證設定）。 |
| [開發者指南](developers/) | 參與 Quart 主程式、播放器、主題或 Bilibili API 封裝開發的貢獻者。 |

## 快速連結

- **使用服務**：開啟所屬實例的網址（本地 Docker 部署的預設位址為 `http://localhost:8000`）。
- **部署實例**：請參閱[使用 Docker 快速啟動](operators/quickstart-docker.md)，僅需兩個指令即可完成部署。
- **理解架構**：請參閱[系統架構](developers/architecture.md)，內容涵蓋 Caddy、Granian／Quart、Redis 與媒體代理的整體設計。
- **原始碼**：[apicalshark/mikuinvidious](https://github.com/apicalshark/mikuinvidious)，採用 GNU GPL-3.0 授權
