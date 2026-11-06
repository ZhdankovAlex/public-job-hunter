# Pipeline and progress

[← README](../README.md)

## Running from a terminal

```bash
.venv\Scripts\python.exe applications.py
```

```bash
.venv\Scripts\python.exe main.py
```

Order matters: mail first, then vacancies — the vacancy report takes application statuses from the fresh
mail cache. `run_daily.bat` runs them the same way. Use the interpreter from `.venv`: the system Python
has no Google libraries, and `applications.py` fails on `import google.auth`.

- `applications.py` takes seconds; the first run (and a run after the token expires) opens a Google
  sign-in window in the browser.
- `main.py` takes from a minute (a few companies) to ~10 minutes (hundreds of sources at 1 request/s per host).

`main.py` flags: `--only "GitLab,PostHog"`, `--limit N`, `--rediscover` (search ATS slugs again),
`--no-aggregators`, `--no-freehire`, `--no-telegram`, `--no-applied` (do not compare with your applications).

## Where to look

| File | What is inside |
|---|---|
| `reports/latest.html` | The main report: vacancies with filters, sorting, Fit %, CV and outreach buttons. Always the last run |
| `reports/jobs_YYYY-MM-DD.html` / `.md` | The same report by date; Markdown is the full text version with all sections |
| `reports/applications.html` | Applications: status per company, mail history, who to follow up with |
| `cv_out/YYYY-MM-DD/` | CVs (MD + PDF) for vacancies — from the "📄 CV" button or the CV page |
| `errors.log` | Warnings and errors of all runs |
| `state/progress.json` | What the run is doing right now (see below) |

## Daily runs

Windows Task Scheduler (the path is `run_daily.bat` in the project folder):

```bash
schtasks /Create /TN "job-hunter" /TR "C:\path\to\job-hunter\run_daily.bat" /SC DAILY /ST 09:00
```

Output goes to `last_run.log`. Remove the task: `schtasks /Delete /TN "job-hunter" /F`.
On Linux / macOS use cron with `.venv/bin/python applications.py && .venv/bin/python main.py`.
In Docker the daily run is set by `JOBHUNTER_DAILY_AT` — see [docker.md](docker.md).

## Stages

Both scripts split the work into stages and keep `state/progress.json` (`jobhunter/progress.py`).

**`main.py`:**

| # | Stage | What it does | Counter |
|---|---|---|---|
| 1 | Company list | `companies.txt` | — |
| 2 | freehire.me: search | Search queries from `settings.toml` | result pages |
| 3 | ATS boards, aggregators, career pages | Greenhouse / Lever / Ashby / Workable / …, Arbeitnow, Remote OK, Himalayas, sites without an ATS — in parallel | sources |
| 4 | Filters and freshness | Role, location, language, vacancy age | vacancies |
| 5 | freehire.me: full texts and checks | Duplicates of found vacancies, missing texts, full-text checks | — |
| 6 | Applications | `applications_manual.toml` and statuses from the mail cache | — |
| 7 | Telegram channels | Web preview t.me/s/… (if enabled) | channels |
| 8 | Telegram vacancy checks | The same full-text checks on the post | vacancies |
| 9 | CV matching, scores, salaries | Fit %, categories, relevance, salary ranges | — |
| 10 | CVs for vacancies | PDFs for new vacancies with a high Fit (off by default) | CVs |
| 11 | Reports | MD, HTML, `latest.html`, `jobs_latest.json` | — |

**`applications.py`:** Gmail: new letters (counter — letters) → applications → applications page.

## Progress line in the terminal

When run in a terminal, a live line is drawn at the bottom and the log is printed above it:

```
[3/11] ATS-борды, агрегаторы, карьерные страницы ████████░░░░░░░░░░░░░░░░ 41/120 34% · ~1:12 · GitLab
```

Stage number and name, a bar by the stage counter, ETA for the stage and the source being processed.
When run by a scheduler or with output redirected to a file, the line is not drawn.

## Pipeline from another window

```bash
.venv\Scripts\python.exe -m jobhunter.progress
```

Redrawn every second while the run is going; `--once` prints one snapshot. Icons: `✔` done, `▶` running,
`·` ahead, `–` skipped (for example `--no-telegram`), `✖` failed. Below are the run counters (collected,
passed filters, new, rejected) and the last 5 warnings. A run whose process ended without finishing
(window closed, process killed) is shown as "interrupted".

## How it works

- A script creates `progress.Pipeline(name, stages)`; `progress.start(stage, total)` starts a stage
  (closing the previous one), `progress.step(what)` is a step, `progress.count(...)` sets the totals.
- Modules (`telegram.py`, `freehire.py`, `mail.py`, `cvgen.py`) call `progress` directly; without an active
  run the calls do nothing, so modules also work on their own (for example `cvgen.py` from the command line).
- Parallel sources are wrapped in `progress.tracked(name, function, …)`: while the request runs, the source
  is listed as "processing now".
- The file is written at most every 0.5 s via a temporary file and `os.replace` — a reader never sees half a
  JSON. Log warnings (WARNING and above) go to `errors` (the last 20).
- An exception or Ctrl+C marks the current stage and the run as `failed`.
- `progress.event(kind, text)` is the event feed (last 60): `found` — a vacancy passed the filters,
  `reject` — rejected with a reason, `source` — a source total. `progress.tally(group, key)` builds breakdowns:
  `rejects` (rejection reasons, grouped in `main._reason_group`) and `source_kinds` (vacancies by source kind).
- After a successful run the stage durations go to `state/progress_history.json` (last 5 runs);
  `progress.eta_total` adds the current stage ETA and the medians of past runs for the remaining stages.

`state/progress.json` format (the web UI reads it):

```json
{
 "script": "main", "pid": 1234, "status": "running",
 "started": "2026-11-05T12:01:13", "updated": "2026-11-05T12:01:17", "elapsed": 4,
 "stage": "sources", "eta_stage": 344, "last_item": "GitLab",
 "active": [{"item": "PostHog", "seconds": 4}],
 "stages": [{"key": "sources", "title": "ATS-борды, …", "status": "running",
             "done": 17, "total": 120, "started": "…", "finished": null, "note": ""}],
 "counters": {"fetched": 0}, "errors": [{"time": "…", "level": "WARNING", "message": "…"}]
}
```
