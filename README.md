# ConfView

A small, self-hosted tracker for academic conference **submission deadlines** and the
surrounding publication milestones (submission → notification → camera-ready → conference).
It is inspired by ETH Zurich's [ConfSearch](https://confsearch.ethz.ch/).

This is a **static [Hugo](https://gohugo.io) site**. The conference data and your
personal tier classification are baked into the page at build time; there is no server
and no database. A separate offline tool re-scrapes the official CFP pages when you ask
it to.

It ships with a curated list of embedded-systems / sensor-networking conferences
(SenSys, EWSN, DATE, DCOSS-IoT, WONS, SAS, COINS, …) and:

- **Only shows conferences whose paper submission deadline is still in the future**
  (tick "Show past deadlines" to reveal past ones; an event with no paper deadline shows
  until it ends). A search box matches name, location, region, tier and topics; it,
  the tier/topic filters and the past/future toggle all run in the browser against
  the baked-in data — no API calls.
- Badges every date as **✓ confirmed** (scraped from the official CFP page) or
  **≈ estimated** (seed/inferred, not yet officially published).
- Shows each conference's **location** and **tier**, on a timeline and detail cards.
- Highlights venues **outside your preferred region** for tiers you opt into (see below).

## Project layout

```
hugo.toml              site config: subtitle, preferredRegion, minify settings
data/
  conferences.json     the conference records (also the scraper's seed + output)
  tiers.yaml           YOUR tier definitions — labels, colours, blurbs, ordering
layouts/index.html     the whole dashboard (template + CSS + client-side JS)
scrapers.py            generic CFP scraper (keyword + date heuristics)
refresh.py             offline tool: scrape -> rewrite data/conferences.json
.github/workflows/refresh.yml  weekly scheduled refresh -> one pull request
.github/workflows/pages.yml    build and publish the site on GitHub Pages
tests/test_scrapers.py  offline tests for the date parsing and merge rules
Dockerfile             reproducible Hugo build (pinned version) -> nginx
Dockerfile.refresh     Python image for the offline refresh
docker-compose.yml     `site` service + on-demand `refresh` service
```

## Run it

### Docker (recommended — reproducible, no host tools)

The Hugo compiler is **pinned to an exact version and checksum inside the image**
(`Dockerfile`), so the build never depends on anything installed on your machine.

```bash
docker compose up --build site
# open http://localhost:8000
```

Or with plain Docker:

```bash
docker build -t confview .
docker run --rm -p 8000:80 confview      # http://localhost:8000
```

### Local (if you already have Hugo installed)

```bash
hugo server            # live-reload dev server, usually http://localhost:1313
hugo --minify          # build static site into ./public
```

## Editing the data

Everything you'd want to change lives in two files under `data/`.

### Conferences — `data/conferences.json`

Each entry looks like:

```json
{
  "id": "dcoss",
  "name": "DCOSS-IoT",
  "full_name": "Distributed Computing in Smart Systems and the Internet of Things",
  "edition_year": 2027,
  "location": "Reykjavik, Iceland",
  "region": "Europe",
  "tier": "B",
  "topics": ["internet of things", "cyber-physical systems"],
  "url_template": "https://dcoss.org/call-for-papers/",
  "sources": ["https://dcoss.org/call-for-papers/", "https://dcoss.org/"],
  "stages": {
    "submission":   { "date": "2027-01-27", "status": "estimated" },
    "notification": { "date": "2027-03-28", "status": "estimated" },
    "camera_ready": { "date": "2027-04-15", "status": "estimated" }
  },
  "conference": { "start": "2027-06-22", "end": "2027-06-24", "status": "estimated" }
}
```

- `tier` — references a tier `id` defined in `data/tiers.yaml`.
- `region` — used by the "outside my region" highlight (see tiers below).
- `sources` — official pages to scrape, in priority order. If `url_template`
  contains `{year}`, the scraper also tries next year's and this year's URL.
- `status` — `confirmed` or `estimated`; the scraper sets dates it parses to `confirmed`.

Rebuild the site (`docker compose up --build site`, or `hugo`) to publish changes.

### Your tier classification — `data/tiers.yaml`

**This is the file to edit when you want to change what your tiers mean.** Rename them,
recolour them, reorder them, add a new tier, or change the one-line description shown in
the legend. Order in the file = order in the legend and the tier filter.

```yaml
- id: A
  label: "Tier A — always submit"      # shown in the filter dropdown
  short: "A"                            # tiny badge text on cards
  color: "#6a3fd0"                      # accent/text colour
  bg: "#ede7fb"                         # badge background
  blurb: "Top venues I target regardless of location."   # legend explanation

- id: B
  label: "Tier B — worth it near home"
  short: "B"
  color: "#1c6f67"
  bg: "#e9f4f2"
  blurb: "Solid venues; I prefer these when they're in my region."
  flag_outside_region: true            # highlight editions outside preferredRegion
```

`preferredRegion` (your "home" region) is set in `hugo.toml` under `[params]`. Any
conference in a tier with `flag_outside_region: true` whose `region` isn't your
preferred region (and isn't `"TBD"`) gets highlighted as a travel nudge.

## Refreshing the dates (offline scraper)

The site is static, so there's no background job. Run the scraper when you want fresh
dates, then rebuild:

```bash
# In Docker (self-contained):
docker compose run --rm refresh          # scrapes -> updates ./data/conferences.json
docker compose up --build site           # rebuild to publish the new dates

# Or locally:
pip install -r requirements.txt
python refresh.py            # scrape all sites, rewrite data/conferences.json
python refresh.py --list     # just print current data, sorted by deadline
python refresh.py --dry-run  # scrape and summarise without writing
python refresh.py --fail-under 5   # exit 1 if fewer than 5 pages parsed
python tests/test_scrapers.py      # offline tests, no network needed
```

`scrapers.py` fetches each conference's CFP page, strips it to text, and locates the
"Important Dates" lines by keyword (skipping workshop, poster, abstract and similar
deadlines). It reads month-first, day-first and ISO dates, and numeric dates: dotted
ones (`27.01.2026`, `22.-24.06.2026`) as day-first; slash ones (`03/04/2026`) only when
one number is above 12 or the page's other dates show whether it writes day-first or
month-first, otherwise the date is skipped rather than guessed. It handles inline
(`Paper Submission: January 27, 2026`) and table layouts, and keeps the **last** date on
a line when a deadline was revised. In a table layout (label on one line, date below)
only a bare date line or a line of the same stage is accepted; any other labelled line
(abstract, registration, workshop, another milestone) ends the lookup, so a side-event
date is never taken as the paper deadline. Rules that keep the data from getting worse:

- Within an edition, a date already marked `confirmed` is **never overwritten**. If
  the page disagrees (often an extended deadline, sometimes a mis-read), the run prints
  a `CONFLICT` line and you decide.
- `estimated` dates become `confirmed` when the page shows them.
- A page about an older edition is ignored, so the edition year never goes backwards.
  A page about a newer edition rolls the entry forward, replacing its dates; stages the
  page doesn't show are moved by a year and marked `estimated`. Because that replaces
  confirmed dates, it needs strong evidence and moves the entry **at most one edition
  per run**: either conference dates from a labelled line in the next year together with
  a paper deadline newer than the seed's and within 15 months before them, or (page
  with a deadline but no conference dates) a paper deadline that falls after the seed's
  conference start and no more than one year after it. A page that claims a later
  edition without meeting that bar is dropped entirely, none of its dates are merged.
  An entry that has fallen more than one edition behind is fixed by hand. Rolled
  entries are listed first in the workflow's job summary.
- Every date taken from a page must fit the edition: no later than its conference
  start and no more than 15 months before it. A date range that was only guessed from
  an unlabelled line (e.g. a registration period) is never written as the conference
  dates; only a labelled "conference dates" line sets them.
- Stages missing from an entry are never invented, and aggregator sites in `sources`
  (myhuiban, wikicfp, conferenceindex, dblp and its mirrors, matched on the hostname
  and its subdomains, case-insensitively) are not scraped.
- If a page can't be reached or parsed, the entry is left as is.

The file is rewritten in its hand-edited layout, and `last_updated` changes only when a
date value did (a status-only change, such as an estimated date being confirmed as is,
is saved without touching it), so a run that finds nothing new leaves the file
byte-for-byte unchanged. The data is replaced in place — one entry per conference, no
history or timestamps — so it does not grow over time. The seed's edition is taken from
the year of its conference start, so keep `edition_year` equal to that year. Scraping
third-party sites is inherently brittle; treat the result as a proposal to review, not
as truth.

### Hosting on GitHub Pages

`.github/workflows/pages.yml` builds the site with the same pinned Hugo as the
Dockerfile and publishes it on every push to the default branch. To turn it on, set
Settings → Pages → Source to **GitHub Actions**. The site then lives at
`https://<user>.github.io/<repo>/`; the base URL is filled in at build time, so
`hugo.toml` needs no change. Together with the weekly refresh below, the loop is:
scraper opens a PR → you review and merge → the site republishes.

### Automatic weekly refresh (GitHub Actions)

`.github/workflows/refresh.yml` runs the scraper every Monday (and on demand from the
Actions tab). If any date changed, it opens a pull request from the fixed branch
`auto/refresh-data`, or updates the one already open, so PRs never pile up. The job
summary lists parsed pages and any `CONFLICT` lines. If fewer than 5 pages parse (for
example, no network), the job fails and opens nothing. If you fork the repository, set
your own URL in `USER_AGENT` in `scrapers.py`. Actions must be allowed to create pull
requests (Settings → Actions → General → Workflow permissions).

## Upgrading Hugo

Bump `HUGO_VERSION` in `Dockerfile` and replace the two `HUGO_SHA256_*` values with the
checksums from the matching GitHub release (`hugo_<version>_checksums.txt`). The pinned
version + checksum are what make the build reproducible. The base images in both
Dockerfiles are pinned by digest (`image:tag@sha256:...`); Dependabot proposes digest
bumps monthly, or look one up with `docker buildx imagetools inspect <image:tag>`.

## License

MIT, see [LICENSE](LICENSE).
