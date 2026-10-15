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

## Demo media ("See it run")

The home page's "See it run" section is a replay of one real ART run on a synthetic student, plus the target role and the before and after resume from that run. It is not a screen recording: a real Claude Code session cannot be recorded in a repeatable way, so the run is captured as data and replayed.

The inputs are synthetic and live in `scripts/site_demo/`: `sample_resume.md` (the resume Riley sends everywhere), `sample_repos.md` (the READMEs of three repos that are not on it) and `sample_posting.txt` (a made-up Data Science Intern posting at an invented company). The graph holds more than fits on a page. **Before** is Riley's usual one-page resume: version 1 of the job, the content of `resume.md` with the three repo projects left off and nothing reworded, built through `execute_plan` and rendered by ART. **After** is version 3 of the same job, the executor's committed version after the gates. Both are whole pages; the build script stops if either page's last line of text ends above the bottom tenth of the page, and `tests/test_site_demo.py` checks the images themselves.

What is captured and what is written:

- **Captured:** every ART tool call's name and what ART answered, driven through the real `art` entry point (`python -m harness.entry <tool> --args <json>`) in the order `/art:setup` and `/art:tailor` take, against a scratch `ART_DATA_DIR`, with Jev off. The two resume PDFs are rendered by ART's own `render` tool with tectonic or pdflatex. The change groups on the page ("Added from your graph", "Moved up", "Reworded", "Cut", "Skills trimmed") are computed from the two versions' stored content and ART's own `diff_nodes` answer, and the build script asserts the two agree.
- **Written by hand** (`scripts/site_demo/demo_script.py`): the lines the user types, the agent's narration, the records the agent extracts from the sample files, the requirements it reads out of the posting (the posting card), and the plan it drafts. The reasons shown for cuts are also written by hand. Which requirement a change serves is matched from the posting terms the plan names for that item (`keywords`) against the terms each requirement lists. The page says so. One bullet in the first draft deliberately carries a figure the sources do not state (a relative lift the agent worked out), so the replay shows ART's consistency gate rejecting it; the replay marks that draft as written for the demo.
- **Shortened for display:** long tool answers lose list items, long strings and fields, each marked with an ellipsis. Playback pacing is a fixed rate, not the real duration of a call.

Refresh it when ART's output changes (a tool's answer, a gate's message, the resume template):

```bash
python scripts/build_site_demo.py --tectonic /path/to/tectonic     # or put tectonic / pdflatex on PATH
python scripts/build_site_demo.py --keep /some/scratch/dir         # also keep the PDFs, .tex and raw answers
```

It rewrites `src/components/demo/transcript.json`, `src/components/demo/before-after.json` and `public/demo/resume-*.webp`. Tune the profile or the plan if a page stops filling. Never point it at your own data: it makes a new temporary data directory, drops `DATABASE_URL`, API keys and `ART_*` variables from the environment, and refuses a data directory under `~/.art`. If the authored narration no longer matches what ART answers (say the gate's verdict changes), it stops with an assertion rather than writing a replay that lies. `tests/test_site_demo.py` checks the committed files without running ART.

The replay is plain text in the page (selectable, readable with scripts off) and a small script reveals it step by step. With `prefers-reduced-motion` it starts in its final state and never autoplays. The home page must stay under 2 MB, counting `index.html` and everything it references; `npm run check:weight` checks `dist/` after a build, and CI runs it.

## Deploy (Vercel)

Hosting is Vercel (Hobby plan) with the Git integration. One-time setup in the Vercel dashboard:

1. Import the `nathansso/agentic_resume_tailoring` repository (grant the Vercel GitHub app access to it).
2. Set **Root Directory** to `site`.
3. Set **Framework Preset** to **Astro**. Build command `npm run build` and output `dist` are the defaults.
4. Set **Node.js Version** to 24.x (Project Settings, General).
5. Deploy. `main` becomes the production deployment and each PR gets a preview URL.

`vercel.json` carries an `ignoreCommand` that runs from the root directory and diffs `site/` against `$VERCEL_GIT_PREVIOUS_SHA`, the commit of the last successful deployment on that branch. A push that changed nothing under `site/` since then skips the build. With no previous deployment, or when that commit isn't in Vercel's shallow clone, the command fails and the build runs. `tests/test_site.py` runs the command against a scratch git repo.

Hobby is for non-commercial use. If ARTie ever earns money, move to Vercel Pro or to Cloudflare Pages.
