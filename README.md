# mikuinvidious docs (`docs` branch)

Documentation source for the MikuInvidious site, built with
[VitePress](https://vitepress.dev) and published to GitHub Pages.

- Default language is English (`/`, `lang: en-US`).
- Traditional Chinese lives under `/zh-TW/`.
- Project pages base: `/mikuinvidious/`.

## Preview locally

```bash
npm install
npm run dev
```

## Deploy

Push to the `docs` branch. The `Docs` workflow (`.github/workflows/docs.yml`)
builds the site and deploys to the `gh-pages` environment. No manual steps.
