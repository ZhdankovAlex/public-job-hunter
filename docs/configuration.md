# Files and settings

[← README](../README.md)

## Entry points

| File | Purpose |
|---|---|
| `main.py` | Collect vacancies and build the report (see [pipeline.md](pipeline.md)) |
| `applications.py` | Application statuses from Gmail → `reports/applications.html` |
| `cvgen.py` | A CV for a vacancy from the command line: `--text/--company/--title` → MD, `--render` → PDF |
| `run_daily.bat` | Daily run on Windows: `applications.py`, then `main.py`, output to `last_run.log` |
| `web/` | Local web UI: `python -m web` |

## Settings (edited by hand)

| File | What is inside |
|---|---|
| `settings.toml` | Almost everything: home country, locations and relocation, freehire queries, sources, Telegram, `[manual_jobs]`, mail company aliases, career pages, thresholds |
| `companies.txt` | Companies to check: `Company \| priority 0–3 \| note` |
| `profile.toml` | Profile for the relevance score: skills with weights and patterns, adjustments |
| `cv/base.toml` | The CV base: experience, projects, skills, interview stories, cover letter template — every CV and letter is built from it |
| `applications_manual.toml` | Applications not from mail (Telegram, LinkedIn, calls): events per company |
| `staffing_blacklist.txt` | Staffing / outsourcing companies — moved to a separate report appendix |
| `ats_mapping.json` | Cache company → ATS and slug, created automatically; can be edited (see [sources.md](sources.md)) |
| `gmail_client.json` | Google OAuth client (a secret, in `.gitignore`) |

## Personal files and examples

Personal files never go to git (`.gitignore`): `settings.toml`, `companies.txt`, `profile.toml`,
`profile_overrides.json`, `staffing_blacklist.txt`, `applications_manual.toml`, `cv/base.toml`, `cv/*.pdf`,
`ats_mapping.json`, logs, `state/`, `reports/`, `cv_out/`, Google credentials.

The repository has examples: `settings.example.toml`, `companies.example.txt`, `profile.example.toml`,
`staffing_blacklist.example.txt`, `cv/base.example.toml`, `applications_manual.example.toml`. While your own
file does not exist, the program reads the example (`config.local_or_example`). To customise, copy the example
without `.example`:

```bash
copy settings.example.toml settings.toml
```

`applications_manual.toml` is created automatically by the first event added in the web UI.

## Key settings

```toml
[home]
country = "Portugal"          # vacancies here (office, hybrid, remote) count like worldwide remote
iso = "PT"                    # for country searches on Himalayas / freehire
places = ["Lisbon", "Porto"]  # cities and other spellings

relocation_countries = ["Netherlands", "Germany", "Spain"]
any_country = true            # relocation anywhere, but only when the employer pays for it

[filters]
hide_russia_only = false      # hide "remote only from Russia" vacancies (Russian-language sources)

[sources]
disabled = ["habr career", "hirehi", "aiven", "bolt", …]   # site adapters not to call

[telegram]
enabled = false
channels = ["pythonrabota", "job_python"]
```

All keys are commented in `settings.example.toml`.

## Code (`jobhunter/`)

| Module | What it does |
|---|---|
| `config.py` | Paths, home country, keywords, location and language regexes, Telegram settings |
| `geo.py` | Reads `settings.toml`, location and relocation rules |
| `http.py` | HTTP client: 1 request/s per host, robots.txt check |
| `progress.py` | Run stages, `state/progress.json`, the terminal progress line, pipeline viewer |
| `companies.py` | Company list and ATS slug candidates |
| `ats.py` | Public JSON APIs of ATS boards (Greenhouse, Lever, Ashby, Workable, …) and slug discovery |
| `bigtech.py` | Career sites of large companies: Microsoft, Workday, Oracle, Amazon |
| `aggregators.py`, `sites.py` | Arbeitnow, Remote OK, Himalayas; job boards and company sites without an ATS |
| `freehire.py` | freehire.me |
| `telegram.py` | Telegram channels via the web preview, a parser per channel format |
| `filters.py`, `verify.py`, `language.py` | Selection: role, location, primary language, full-text checks |
| `staffing.py` | Staffing / outsourcing blacklist |
| `keys.py` | Normalised keys: vacancy URL, IDs in a URL, company name, ATS board from a link |
| `applied.py` | Vacancies and companies you already applied to (`applications_manual.toml`) |
| `mail.py`, `applications_report.py` | Gmail (read-only), letter parsing, the applications page |
| `cv_match.py`, `relevance.py`, `tiers.py`, `salary.py`, `levels.py` | Fit % to the CV, score, category, grade, salary ranges, levels.fyi |
| `cvgen.py` | CV builder: picks bullets for a vacancy, one-page version, Markdown → HTML → PDF via headless Chrome |
| `cover_letter.py` | Cover letter for a vacancy from `cv/base.toml` (`[summary]`, `[cover]`) |
| `vacancy_lookup.py` | A vacancy by link: from saved reports or from the page (JobPosting) |
| `outreach.py` | LinkedIn people-search links, contacts from the text, message drafts |
| `interview_prep.py`, `market_check.py` | Interview preparation, CV vs market |
| `report.py`, `html_report.py` | Markdown and HTML reports |
| `models.py` | `Job`, `Company`, date and HTML parsing |

## Data between runs (`state/`, in `.gitignore`)

| File | What it keeps |
|---|---|
| `progress.json` | State of the current / last run |
| `progress_history.json` | Stage durations of recent runs — for the time-left estimate |
| `progress_control.json` | A request from the web UI to skip a stage of the running run |
| `jobs/<date>.json` | Vacancies of every report with text — for CVs from old reports |
| `jobs_latest.json` | Vacancies of the last report with text — for `cvgen.py` |
| `sources_latest.json` | How many vacancies every source gave in the last run, errors |
| `freehire_latest.json`, `freehire_jobs.json`, `freehire_cache/` | freehire queries and responses |
| `seen.json` | When a vacancy first appeared (the 🆕 mark) |
| `rejected_latest.tsv` | Everything the filters dropped, with the reason and a quote |
| `mail_cache.json`, `mail_sync.json`, `gmail_token.json` | Mail cache, last sync, Gmail token |
| `salary_samples.json`, `levels_cache.json` | Salary ranges base, levels.fyi cache |
| `cv_text.json` | CV text for matching |

`reports/`, `cv_out/`, `errors.log`, `errors.prev.log`, `last_run.log` are run results; they can be deleted, the
next run rebuilds them.
