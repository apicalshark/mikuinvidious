import { defineConfig } from "vitepress";
import { withMermaid } from "vitepress-plugin-mermaid";

const operatorSidebar = [
  { text: "Overview", link: "/operators/" },
  { text: "Quickstart with Docker", link: "/operators/quickstart-docker" },
  { text: "Manual installation", link: "/operators/manual-install" },
  { text: "Configuration reference", link: "/operators/configuration" },
  { text: "Reverse proxy (Caddy)", link: "/operators/reverse-proxy" },
  { text: "Bilibili credentials", link: "/operators/credentials" },
  { text: "Caching", link: "/operators/caching" },
  { text: "Rate limiting", link: "/operators/rate-limiting" },
  { text: "Maintenance", link: "/operators/maintenance" },
  { text: "Troubleshooting", link: "/operators/troubleshooting" },
];

const developerSidebar = [
  { text: "Overview", link: "/developers/" },
  { text: "Architecture", link: "/developers/architecture" },
  { text: "Playback & download", link: "/developers/playback-stack" },
  { text: "Route & API reference", link: "/developers/api-reference" },
  { text: "Bilibili API wrapper", link: "/developers/bilibili-api" },
  { text: "Theming", link: "/developers/theming" },
  { text: "Internationalization", link: "/developers/i18n" },
  { text: "Testing", link: "/developers/testing" },
  { text: "Contributing", link: "/developers/contributing" },
];

const operatorSidebarZhTw = [
  { text: "總覽", link: "/zh-TW/operators/" },
  { text: "使用 Docker 快速啟動", link: "/zh-TW/operators/quickstart-docker" },
  { text: "手動安裝", link: "/zh-TW/operators/manual-install" },
  { text: "組態參照", link: "/zh-TW/operators/configuration" },
  { text: "反向代理（Caddy）", link: "/zh-TW/operators/reverse-proxy" },
  { text: "Bilibili 憑證", link: "/zh-TW/operators/credentials" },
  { text: "快取", link: "/zh-TW/operators/caching" },
  { text: "速率限制", link: "/zh-TW/operators/rate-limiting" },
  { text: "日常維運", link: "/zh-TW/operators/maintenance" },
  { text: "故障排除", link: "/zh-TW/operators/troubleshooting" },
];

const developerSidebarZhTw = [
  { text: "總覽", link: "/zh-TW/developers/" },
  { text: "系統架構", link: "/zh-TW/developers/architecture" },
  { text: "播放與下載架構", link: "/zh-TW/developers/playback-stack" },
  { text: "路由與 API 參照", link: "/zh-TW/developers/api-reference" },
  { text: "Bilibili API 封裝", link: "/zh-TW/developers/bilibili-api" },
  { text: "主題開發", link: "/zh-TW/developers/theming" },
  { text: "多國語系", link: "/zh-TW/developers/i18n" },
  { text: "測試", link: "/zh-TW/developers/testing" },
  { text: "參與貢獻", link: "/zh-TW/developers/contributing" },
];

export default withMermaid(
  defineConfig({
    base: "/mikuinvidious/",
    srcExclude: ["README.md"],
    vite: {
      // mermaid pulls a UMD-only deep import
      // (fastdom/extensions/fastdom-promised.js, no ESM exports).
      // Pre-bundle it so esbuild resolves the interop in dev;
      // production Rollup already handles this (build was never broken).
      optimizeDeps: {
        include: ["mermaid", "fastdom/extensions/fastdom-promised.js"],
      },
    },
    title: "MikuInvidious Docs",
    description: "MikuInvidious operator and developer docs",
    locales: {
      root: {
        label: "English",
        lang: "en-US",
        themeConfig: {
          nav: [
            { text: "Operator guide", link: "/operators/" },
            { text: "Developer guide", link: "/developers/" },
          ],
          sidebar: {
            "/": [
              {
                text: "Docs",
                items: [
                  { text: "Home", link: "/" },
                  { text: "Operator guide", link: "/operators/" },
                  { text: "Developer guide", link: "/developers/" },
                ],
              },
            ],
            "/operators/": [
              { text: "Operator guide", items: operatorSidebar },
            ],
            "/developers/": [
              { text: "Developer guide", items: developerSidebar },
            ],
          },
          search: { provider: "local" },
        },
      },
      "zh-TW": {
        label: "正體中文",
        lang: "zh-TW",
        link: "/zh-TW/",
        title: "MikuInvidious 文件",
        description: "MikuInvidious 站長與開發者文件（正體中文）",
        themeConfig: {
          nav: [
            { text: "站長指南", link: "/zh-TW/operators/" },
            { text: "開發者指南", link: "/zh-TW/developers/" },
          ],
          sidebar: {
            "/zh-TW/": [
              {
                text: "指南",
                items: [
                  { text: "首頁", link: "/zh-TW/" },
                  { text: "站長指南", link: "/zh-TW/operators/" },
                  { text: "開發者指南", link: "/zh-TW/developers/" },
                ],
              },
            ],
            "/zh-TW/operators/": [
              { text: "站長指南", items: operatorSidebarZhTw },
            ],
            "/zh-TW/developers/": [
              { text: "開發者指南", items: developerSidebarZhTw },
            ],
          },
          search: {
            provider: "local",
            options: {
              translations: {
                button: {
                  buttonText: "搜尋文件",
                  buttonAriaLabel: "搜尋文件",
                },
                modal: {
                  noResultsText: "找不到相關結果",
                  resetButtonTitle: "清除查詢條件",
                  footer: {
                    selectText: "選擇",
                    navigateText: "切換",
                    closeText: "關閉",
                  },
                },
              },
            },
          },
          outline: { label: "本頁目錄" },
          docFooter: { prev: "上一頁", next: "下一頁" },
          langMenuLabel: "切換語言",
          returnToTopLabel: "回到頂端",
          sidebarMenuLabel: "選單",
          darkModeSwitchLabel: "切換深色模式",
        },
      },
    },
    themeConfig: {
      socialLinks: [
        { icon: "github", link: "https://github.com/apicalshark/mikuinvidious" },
      ],
    },
  })
);
