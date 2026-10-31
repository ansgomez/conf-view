# Agent guidelines for ConfView

Instructions for coding agents (Claude, Codex, Kimi, etc.) working in this
repository. Read this file before making changes. README.md covers project
setup and usage for humans; this file covers the rules for working in the repo.

## Git identity: always use the GitHub noreply address

**Every commit in this repository must use the GitHub noreply identity, never
the global git config** (the global email is a private work address that must
not appear in public history):

```
Andres Gomez <8250065+ansgomez@users.noreply.github.com>
```

- Before the first commit in a clone, set it locally for the repo (this
  overrides `~/.gitconfig` for this repository only):
  ```bash
  git config user.name  "Andres Gomez"
  git config user.email "8250065+ansgomez@users.noreply.github.com"
  ```
- Verify before committing: `git config user.email` must print the noreply
  address. If it prints anything else, stop and fix it first.
- If a commit was already made with another address, amend it before pushing:
  `git commit --amend --no-edit --reset-author` (after setting the local config).
- Never run `git config --global ...` to change this; the local config is enough.
- Commit or push only when asked. Work on a branch, not directly on `main`.

## Project in one paragraph

A single-page static Hugo site that tracks academic conference submission
deadlines. Conference records live in `data/conferences.json` and tier
definitions in `data/tiers.yaml`; Hugo bakes both into `layouts/index.html` as
JSON `<script type="application/json">` blocks, and `static/js/app.js` renders
the timeline and cards in the browser. `scrapers.py` + `refresh.py` are an
offline Python tool that re-scrapes official CFP pages; a weekly GitHub Actions
job opens one PR with the changes, and `pages.yml` publishes to GitHub Pages.

## Commands

```bash
hugo server                            # dev server, http://localhost:1313
hugo --minify --panicOnWarning         # build (CI treats warnings as errors)
python tests/test_scrapers.py          # offline tests, no network
pip install --require-hashes -r requirements.txt
docker compose up --build site         # http://localhost:8000
```

Run the tests and a `--panicOnWarning` build before saying a change is done.
For UI changes, also look at the page in both themes and at phone width
(~390 px): headless Chrome screenshots are fine. Serve the build over HTTP
(e.g. `python3 -m http.server`), not `file://`, because the CSP uses `'self'`.

## Rules for the site

- **No inline JavaScript.** The Content-Security-Policy is `script-src 'self'`
  (meta tag in `layouts/index.html`, header in `nginx.conf`; keep the two in
  sync). Put code in `static/js/`. No inline event handlers (`onclick=` etc.).
- **Escape everything from data.** Any data value that goes into `innerHTML`
  goes through `esc()`. Colours from `tiers.yaml` must pass the hex check
  (`hexColor()` in JS, `findRE` in the template); never interpolate raw CSS.
- **Theme tokens, not hard-coded colours.** Colours are CSS custom properties
  on `:root` with a dark set under `:root[data-theme="dark"]`. A new colour
  needs both a light and a dark value.
- **Contrast.** Text tokens must meet WCAG AA (4.5:1) on the surfaces they are
  used on, in both themes. Compute the ratio; don't eyeball it.
- **Theme state.** `static/js/theme.js` runs blocking in `<head>` (no flash of
  the wrong theme). localStorage `cv-theme` is `light`, `dark`, or absent
  (= follow the system). Keep it that way.
- **Accessibility.** Interactive things are focusable and labelled; decorative
  emoji are `aria-hidden`; tooltips must work on focus, not only on hover.
- **Hugo data access** uses `hugo.Data` (needs Hugo ≥ 0.156); `.Site.Data`
  and `site.Data` are deprecated and fail the `--panicOnWarning` build.
- **Logo.** `layouts/partials/logo.html` is the inline wordmark (uses
  `currentColor`); standalone SVGs are in `static/img/`. The centre dot is
  always `#4353FF`; ink is `#14161F` (light) or white (dark).

## Rules for the Python tool

- Keep `tests/test_scrapers.py` a plain script (no pytest dependency). Add a
  `check(...)` for every behaviour change; the network is always stubbed.
- Never run the scraper against real conference sites as a "test"; use
  `--dry-run` only when explicitly asked.
- Dependencies: edit `requirements.in`, then regenerate the hashed
  `requirements.txt` with
  `pip-compile --generate-hashes --strip-extras requirements.in`.
  Don't hand-edit `requirements.txt`.
- `data/conferences.json` is rewritten by `refresh.py` in a fixed layout; if
  you edit it by hand, keep that layout (the "layout round trip" test checks it).

## Pinned versions

- **Hugo** is pinned in three places with checksums: `Dockerfile`,
  `.github/workflows/pages.yml`, `.github/workflows/test.yml`. Change all three
  together, using checksums from the official `hugo_<version>_checksums.txt`.
- **GitHub Actions** are pinned by commit SHA with a version comment. Keep that
  form; never use a bare tag like `@v4`.
- **Docker base images** are pinned by digest. Dependabot bumps them.
- Workflows grant the minimum token permissions; scope write access to the job
  that needs it.
