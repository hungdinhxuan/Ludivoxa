# Ludivoxa documentation website

Build and preview with Node.js 24 LTS.

```sh
npm ci
npm run dev
npm run build
```

Run these commands from `website/`. Vietnamese guides are generated from
[`docs/USER_GUIDE.md`](../docs/USER_GUIDE.md) before each build or dev session.
Edit that guide for Vietnamese instructions, and edit the corresponding English
Markdown under `src/content/docs/en/` for the translation. The site homepages
and benchmark summaries live in `src/content/docs/`.

GitHub Pages uses **GitHub Actions** as its publishing source. The
[documentation workflow](../.github/workflows/docs.yml) builds on pushes to
`main` that touch the website or documentation. It takes the published URL
from Pages so links, canonical URLs and the sitemap follow any domain change.
For a local build targeting the custom domain, set
`PAGES_URL=https://ludivoxa.hungdx.com/` before running `npm run build`.
