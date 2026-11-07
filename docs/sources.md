# Vacancy sources

[← README](../README.md)

## Rules for every source

- Before the first request to a host its `robots.txt` is read; disallowed URLs are not requested.
- At most 1 request per second per host (`jobhunter/http.py`); slower where a site needs it
  (Workable — 1 per 6 s, Telegram — 1 per 2 s).
- LinkedIn is not used.
- Every source is optional; a failing source is logged to `errors.log` and the run goes on.
- `blocked_urls` in `settings.toml` hides specific links from any source.

## ATS boards

`jobhunter/ats.py` reads the official public JSON APIs of Greenhouse, Lever, Ashby, Workable, plus
Teamtailor, Recruitee, BambooHR, Breezy and Rippling. The companies come from `companies.txt`.

- Workable and Rippling list a vacancy with several locations once per location; such records are merged into one
  vacancy with all locations.
- BambooHR, Breezy and Rippling give a list without the text: the text is loaded only for vacancies with a
  matching title (up to `DETAIL_FETCH_LIMIT` per company).

The board slug is guessed from the company name (`GitLab` → `gitlab`, `Grafana Labs` → `grafanalabs`,
`grafana-labs`, `grafana`) on Greenhouse, Lever, Ashby and Workable; the first board with vacancies wins.
Results are cached in `ats_mapping.json`; companies that were not found are re-checked every 30 days.
`--rediscover` searches again for all companies. A company name with Cyrillic look-alike letters ("Acme.аi" with a
Cyrillic "а") is normalised before the slug is guessed.

**A board is gone** (404, 401/403/410, or an HTML page instead of data): the script searches for a new board at once.
Found — the run feed gets "борд переехал old → new" and the mapping is updated; not found — one warning "борда нет"
and the next check in 30 days, so the same warning does not repeat every run. `ats_mapping.json` is saved by merging:
only entries changed by this run are written, edits made while the run was going are kept.

### Fixing `ats_mapping.json`

The slug is a guess, so two kinds of mistakes are possible:

- **not found** (`"platform": null`) — the company uses another slug or another ATS. If you know it, write it:
  `"Example Corp": {"platform": "greenhouse", "slug": "examplecorp"}`.
- **someone else's board** — a company with the same name. Replace the entry with the right one, or with
  `{"platform": null, "checked_at": "2099-01-01"}`.

Rippling boards are not guessed — add them by hand: `{"platform": "rippling", "slug": "examplecorp"}`.

Mark manual edits with `"manual": true`. Teamtailor boards are added by hand:
`{"platform": "teamtailor", "slug": "careers.example.com"}`. A company with its own careers page and no
ATS: `{"platform": null, "manual": true}` and the page in `settings.toml` → `[career_pages]`.
A Greenhouse board can also be forced in `settings.toml` → `[ats_overrides."Company"]`.

## freehire.me

`jobhunter/freehire.py` uses the public API `https://freehire.me/api/v1` (no key). Queries and common
parameters are in `settings.toml` → `[freehire]`.

- At most `max_requests` network requests per run; responses are cached in `state/freehire_cache` for
  `cache_hours` hours.
- `meta.ignored_params` is checked in every response: if the API dropped a parameter, the run stops with
  an error (exit code 2) — otherwise the results would be wider than intended.
- The search uses `/agent/jobs/search` (`description_format=text`), so every vacancy has its full text and
  goes through the same checks as other sources.
- If a query returns 0, the script finds the filter that empties the result (adds parameters one by one and
  checks `/jobs/facets`) and reports it in the summary. An empty value in a query (`reality = ""`) removes a
  common parameter.
- A vacancy found both on freehire and on an ATS is shown once with the source `freehire + greenhouse`;
  the link always points to the original.

## Aggregators and job boards

- Arbeitnow, Remote OK, Himalayas (`jobhunter/aggregators.py`). Himalayas' `robots.txt` disallows `page=`,
  so instead of paging the script runs several narrow queries (`HIMALAYAS_QUERIES`), one fresh page each;
  with `[home].iso` set, it also searches the home country.
- Job boards in `jobhunter/sites.py` (addresses and page counts in `settings.toml` → `[boards]`):
  Remote Rocketship, Brenxor, justjoin.it, remote.com, Cryptocurrency Jobs (RSS), relocate.me.
  Russian-language boards: Habr Career and HireHi (disabled by default in `[sources].disabled`).
- Company sites without a public ATS, as examples of adapters: Aiven (`aiven.io/careers/job.data`), Bolt, fin.dev,
  Gurtam, Rivian and Paychex (iCIMS Jibe JSON), 01.tech (disabled by default — remove a name from `[sources].disabled`
  to use it).
- Any source from `sites.SOURCES` can be switched off by name in `settings.toml` → `[sources].disabled`.

## Large companies

`jobhunter/bigtech.py` (settings in `[bigtech]`): Microsoft, Workday sites (NVIDIA, Salesforce, Adobe, …),
Oracle Recruiting Cloud and Amazon. The search uses the sites' own JSON endpoints with `queries`; the full
text is opened only for vacancies with a matching title, at most `details_per_company` per company (home
country and remote first, then the freshest). Large companies rarely mention relocation in the text, so their
vacancies in `relocation_hubs` countries go to 🌍 as "relocation (unconfirmed)" — ask the recruiter.

## Career pages and single vacancies

- `[career_pages]`: `Company = "url"` or `{ url = "...", location = "Remote" }` (the company must be in
  `companies.txt`). `sites.career_page` looks for links with matching titles and opens them: Python / backend
  first, up to `PAGE_DETAIL_LIMIT` (100); if there are more, a warning says "открыто N из M". A generic "Apply" button
  takes the title from the link address (`/job/senior-python-developer`). Staffing companies
  (`staffing_blacklist.txt`) are not crawled. Pages that build the list with scripts cannot be read this way.
- `[manual_jobs].urls`: vacancies you found by hand. They are opened every run and go to the report with the
  same filters, Fit and application status; the source is "вручную" (manual). Data comes from JSON-LD
  `JobPosting`, else Next.js page data, else `<title>` and the page text.

## Telegram channels (optional)

Disabled by default: `settings.toml` → `[telegram].enabled = true` and a `channels` list. Channels are read via
the public web preview `https://t.me/s/<channel>` (paging back with `?before=`), no deeper than `max_age_days`
and no more often than 1 request per 2 seconds. Telethon and the Telegram API are not used. `--no-telegram`
skips them for one run.

`jobhunter/telegram.py` has parsers for several Russian-language channel formats (`PARSERS`): one post =
one vacancy, digests split by "Apply" links, posts with a link to an ATS (the full text is then taken from the
board), posts with a telegra.ph link (the text comes from `api.telegra.ph/getPage`), and others. Channels without
their own parser use the generic one. Hosts in `[telegram].aggregator_hosts` get a liveness check of the link.

The result is the "Telegram-каналы" section of the report: `✅` first, then `❓`, by date. With
`[filters].hide_russia_only = true`, posts where remote work is possible only from Russia are hidden.
