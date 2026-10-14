# ARTie site

The public landing and docs site for ARTie. A standalone Astro + Tailwind (v4) static site: it shares no build with `web/frontend` and is never packaged into `art-mcp`.

## Develop

Needs Node 24 (the repo's CI and Dockerfile use it).

```bash
cd site
npm ci
npm run dev       # http://localhost:4321
npm run build     # static output in dist/
npm run preview   # serve dist/ locally
```

## Theme

`src/styles/global.css` ports the light and dark tokens from `web/frontend/src/index.css`. Dark mode is the `.dark` class on `<html>`, set before first paint by the inline script in `src/layouts/Base.astro`. `tests/test_site.py` fails if the core tokens drift from the app's.

## Deploy (Vercel)

Hosting is Vercel (Hobby plan) with the Git integration. One-time setup in the Vercel dashboard:

1. Import the `nathansso/agentic_resume_tailoring` repository (grant the Vercel GitHub app access to it).
2. Set **Root Directory** to `site`.
3. Set **Framework Preset** to **Astro**. Build command `npm run build` and output `dist` are the defaults.
4. Set **Node.js Version** to 24.x (Project Settings, General).
5. Deploy. `main` becomes the production deployment and each PR gets a preview URL.

`vercel.json` carries an `ignoreCommand` (`git diff --quiet HEAD^ HEAD -- .`), which runs from the root directory, so commits that don't touch `site/` skip the build.

Hobby is for non-commercial use. If ARTie ever earns money, move to Vercel Pro or to Cloudflare Pages.
