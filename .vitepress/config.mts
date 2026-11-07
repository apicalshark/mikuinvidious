import { defineConfig } from "vitepress";
import { withMermaid } from "vitepress-plugin-mermaid";

const SITE_URL = "https://apicalshark.github.io/mikuinvidious";

// Source markdown path -> clean site route (no .html suffix;
// output files are rewritten to directory style by scripts/clean-urls.mjs).
function toRoute(relativePath: string): string | null {
  if (!relativePath.endsWith(".md")) return null;
  let p = relativePath.slice(0, -".md".length);
  if (p === "index") return "/";
  if (p.endsWith("/index")) p = p.slice(0, -"/index".length);
  return `/${p}`;
}

const guideSidebar = [
  { text: "Overview", link: "/guide/" },
  { text: "Bilibili links & search bar", link: "/guide/search-and-links" },
  { text: "URL routes & features", link: "/guide/url-syntax" },
  { text: "Player & downloads", link: "/guide/playback-and-downloads" },
];

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

const guideSidebarZhTw = [
  { text: "總覽", link: "/zh-TW/guide/" },
  { text: "Bilibili 連結與搜尋列", link: "/zh-TW/guide/search-and-links" },
  { text: "網址路由與功能", link: "/zh-TW/guide/url-syntax" },
  { text: "播放器與下載", link: "/zh-TW/guide/playback-and-downloads" },
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

const guideSidebarZhCn = [
  { text: "总览", link: "/zh-CN/guide/" },
  { text: "Bilibili 链接与搜索栏", link: "/zh-CN/guide/search-and-links" },
  { text: "网址路由与功能", link: "/zh-CN/guide/url-syntax" },
  { text: "播放器与下载", link: "/zh-CN/guide/playback-and-downloads" },
];

const operatorSidebarZhCn = [
  { text: "总览", link: "/zh-CN/operators/" },
  { text: "使用 Docker 快速启动", link: "/zh-CN/operators/quickstart-docker" },
  { text: "手动安装", link: "/zh-CN/operators/manual-install" },
  { text: "配置参考", link: "/zh-CN/operators/configuration" },
  { text: "反向代理（Caddy）", link: "/zh-CN/operators/reverse-proxy" },
  { text: "Bilibili 凭证", link: "/zh-CN/operators/credentials" },
  { text: "缓存", link: "/zh-CN/operators/caching" },
  { text: "速率限制", link: "/zh-CN/operators/rate-limiting" },
  { text: "日常运维", link: "/zh-CN/operators/maintenance" },
  { text: "故障排除", link: "/zh-CN/operators/troubleshooting" },
];

const developerSidebarZhCn = [
  { text: "总览", link: "/zh-CN/developers/" },
  { text: "系统架构", link: "/zh-CN/developers/architecture" },
  { text: "播放与下载架构", link: "/zh-CN/developers/playback-stack" },
  { text: "路由与 API 参考", link: "/zh-CN/developers/api-reference" },
  { text: "Bilibili API 封装", link: "/zh-CN/developers/bilibili-api" },
  { text: "主题开发", link: "/zh-CN/developers/theming" },
  { text: "多语言国际化", link: "/zh-CN/developers/i18n" },
  { text: "测试", link: "/zh-CN/developers/testing" },
  { text: "参与贡献", link: "/zh-CN/developers/contributing" },
];

export default withMermaid(
  defineConfig({
    base: "/mikuinvidious/",
    cleanUrls: true,
    head: [
      ["link", { rel: "icon", type: "image/svg+xml", href: "/mikuinvidious/favicon.svg" }],
      ["link", { rel: "icon", type: "image/png", sizes: "32x32", href: "/mikuinvidious/favicon-32x32.png" }],
      ["link", { rel: "icon", type: "image/png", sizes: "16x16", href: "/mikuinvidious/favicon-16x16.png" }],
      ["link", { rel: "apple-touch-icon", sizes: "180x180", href: "/mikuinvidious/apple-touch-icon.png" }],
      ["link", { rel: "manifest", href: "/mikuinvidious/site.webmanifest" }],
      ["meta", { name: "theme-color", content: "#120c10" }],
      // Search-console verification (URL-prefix property -> HTML tag method).
      ["meta", { name: "google-site-verification", content: "O1EOiTZWeJzZq_dAP14Aswo7zN19vX2T8x64ml-KR8g" }],
    ],
    srcExclude: ["README.md"],
    transformHead({ pageData }) {
      const route = toRoute(pageData.relativePath);
      if (!route) return [];
      const isZhTw = pageData.relativePath.startsWith("zh-TW/");
      const isZhCn = pageData.relativePath.startsWith("zh-CN/");
      let subPath = route;
      if (isZhTw) subPath = route.replace(/^\/zh-TW/, "") || "/";
      else if (isZhCn) subPath = route.replace(/^\/zh-CN/, "") || "/";

      const enRoute = subPath;
      const zhTwRoute = subPath === "/" ? "/zh-TW" : `/zh-TW${subPath}`;
      const zhCnRoute = subPath === "/" ? "/zh-CN" : `/zh-CN${subPath}`;

      const title = pageData.title || "MikuInvidious Docs";
      const description =
        pageData.description ||
        (isZhTw
          ? "MikuInvidious 使用者、站長與開發者文件"
          : isZhCn
          ? "MikuInvidious 用户、站长与开发者文档"
          : "MikuInvidious user, operator, and developer docs");
      const url = `${SITE_URL}${route === "/" ? "/" : `${route}/`}`;
      const locale = isZhTw ? "zh_TW" : isZhCn ? "zh_CN" : "en_US";
      const altLocales = ["en_US", "zh_TW", "zh_CN"].filter((l) => l !== locale);

      return [
        ["link", { rel: "canonical", href: url }],
        ["meta", { property: "og:title", content: title }],
        ["meta", { property: "og:description", content: description }],
        ["meta", { property: "og:url", content: url }],
        ["meta", { property: "og:locale", content: locale }],
        ...altLocales.map((alt) => [
          "meta",
          { property: "og:locale:alternate", content: alt },
        ]),
        [
          "link",
          {
            rel: "alternate",
            hreflang: "en",
            href: `${SITE_URL}${enRoute === "/" ? "/" : `${enRoute}/`}`,
          },
        ],
        [
          "link",
          {
            rel: "alternate",
            hreflang: "zh-TW",
            href: `${SITE_URL}${zhTwRoute}/`,
          },
        ],
        [
          "link",
          {
            rel: "alternate",
            hreflang: "zh-CN",
            href: `${SITE_URL}${zhCnRoute}/`,
          },
        ],
        [
          "link",
          {
            rel: "alternate",
            hreflang: "x-default",
            href: `${SITE_URL}${enRoute === "/" ? "/" : `${enRoute}/`}`,
          },
        ],
      ];
    },
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
    description: "MikuInvidious user, operator, and developer docs",
    locales: {
      root: {
        label: "English",
        lang: "en-US",
        themeConfig: {
          nav: [
            { text: "User guide", link: "/guide/" },
            { text: "Operator guide", link: "/operators/" },
            { text: "Developer guide", link: "/developers/" },
            { text: "Instances", link: "/instances/" },
          ],
          sidebar: {
            "/": [
              {
                text: "Docs",
                items: [
                  { text: "Home", link: "/" },
                  { text: "User guide", link: "/guide/" },
                  { text: "Operator guide", link: "/operators/" },
                  { text: "Developer guide", link: "/developers/" },
                  { text: "Instances", link: "/instances/" },
                ],
              },
            ],
            "/guide/": [
              { text: "User guide", items: guideSidebar },
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
        description: "MikuInvidious 使用者、站長與開發者文件（正體中文）",
        themeConfig: {
          nav: [
            { text: "使用者指南", link: "/zh-TW/guide/" },
            { text: "站長指南", link: "/zh-TW/operators/" },
            { text: "開發者指南", link: "/zh-TW/developers/" },
            { text: "公開實例", link: "/zh-TW/instances/" },
          ],
          sidebar: {
            "/zh-TW/": [
              {
                text: "指南",
                items: [
                  { text: "首頁", link: "/zh-TW/" },
                  { text: "使用者指南", link: "/zh-TW/guide/" },
                  { text: "站長指南", link: "/zh-TW/operators/" },
                  { text: "開發者指南", link: "/zh-TW/developers/" },
                  { text: "公開實例", link: "/zh-TW/instances/" },
                ],
              },
            ],
            "/zh-TW/guide/": [
              { text: "使用者指南", items: guideSidebarZhTw },
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
      "zh-CN": {
        label: "简体中文",
        lang: "zh-CN",
        link: "/zh-CN/",
        title: "MikuInvidious 文档",
        description: "MikuInvidious 用户、站长与开发者文档（简体中文）",
        themeConfig: {
          nav: [
            { text: "用户指南", link: "/zh-CN/guide/" },
            { text: "站长指南", link: "/zh-CN/operators/" },
            { text: "开发者指南", link: "/zh-CN/developers/" },
            { text: "公开实例", link: "/zh-CN/instances/" },
          ],
          sidebar: {
            "/zh-CN/": [
              {
                text: "指南",
                items: [
                  { text: "首页", link: "/zh-CN/" },
                  { text: "用户指南", link: "/zh-CN/guide/" },
                  { text: "站长指南", link: "/zh-CN/operators/" },
                  { text: "开发者指南", link: "/zh-CN/developers/" },
                  { text: "公开实例", link: "/zh-CN/instances/" },
                ],
              },
            ],
            "/zh-CN/guide/": [
              { text: "用户指南", items: guideSidebarZhCn },
            ],
            "/zh-CN/operators/": [
              { text: "站长指南", items: operatorSidebarZhCn },
            ],
            "/zh-CN/developers/": [
              { text: "开发者指南", items: developerSidebarZhCn },
            ],
          },
          search: {
            provider: "local",
            options: {
              translations: {
                button: {
                  buttonText: "搜索文档",
                  buttonAriaLabel: "搜索文档",
                },
                modal: {
                  noResultsText: "找不到相关结果",
                  resetButtonTitle: "清除查询条件",
                  footer: {
                    selectText: "选择",
                    navigateText: "切换",
                    closeText: "关闭",
                  },
                },
              },
            },
          },
          outline: { label: "本页目录" },
          docFooter: { prev: "上一页", next: "下一页" },
          langMenuLabel: "切换语言",
          returnToTopLabel: "回到顶部",
          sidebarMenuLabel: "菜单",
          darkModeSwitchLabel: "切换深色模式",
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
