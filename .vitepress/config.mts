import { defineConfig } from "vitepress";
import { withMermaid } from "vitepress-plugin-mermaid";

const operatorSidebar = [
  { text: "總覽", link: "/operators/" },
  { text: "使用 Docker 快速啟動", link: "/operators/quickstart-docker" },
  { text: "手動安裝", link: "/operators/manual-install" },
  { text: "組態參照", link: "/operators/configuration" },
  { text: "反向代理（Caddy）", link: "/operators/reverse-proxy" },
  { text: "Bilibili 憑證", link: "/operators/credentials" },
  { text: "快取", link: "/operators/caching" },
  { text: "速率限制", link: "/operators/rate-limiting" },
  { text: "日常維運", link: "/operators/maintenance" },
  { text: "故障排除", link: "/operators/troubleshooting" },
];

const developerSidebar = [
  { text: "總覽", link: "/developers/" },
  { text: "系統架構", link: "/developers/architecture" },
  { text: "播放與下載架構", link: "/developers/playback-stack" },
  { text: "路由與 API 參照", link: "/developers/api-reference" },
  { text: "Bilibili API 封裝", link: "/developers/bilibili-api" },
  { text: "主題開發", link: "/developers/theming" },
  { text: "多國語系", link: "/developers/i18n" },
  { text: "測試", link: "/developers/testing" },
  { text: "參與貢獻", link: "/developers/contributing" },
];

export default withMermaid(
  defineConfig({
    base: "/mikuinvidious/",
    srcExclude: ["README.md"],
    title: "MikuInvidious 文件",
    description: "MikuInvidious 站長與開發者文件（正體中文）",
    locales: {
      root: {
        label: "正體中文",
        lang: "zh-TW",
        themeConfig: {
          nav: [
            { text: "站長指南", link: "/operators/" },
            { text: "開發者指南", link: "/developers/" },
          ],
          sidebar: {
            "/operators/": [
              { text: "站長指南", items: operatorSidebar },
            ],
            "/developers/": [
              { text: "開發者指南", items: developerSidebar },
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
      en: {
        label: "English",
        lang: "en-US",
        link: "/en/",
        title: "MikuInvidious Docs",
        description: "MikuInvidious operator and developer docs",
        themeConfig: {
          nav: [{ text: "Home", link: "/en/" }],
          sidebar: {
            "/en/": [{ text: "Docs", items: [{ text: "Home", link: "/en/" }] }],
          },
          search: { provider: "local" },
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
