# Web UI

[← README](../README.md)

```bash
.venv\Scripts\python.exe -m web
```

Opens `http://127.0.0.1:8765/` in the browser (`--port N`, `--no-browser`). The server listens on the local
address only: reports contain mail, applications and contacts. API docs — `http://127.0.0.1:8765/docs`.
The interface is in Russian.

| Page | What is there |
|---|---|
| **Пайплайн** (Pipeline) | A live run: stages with bars, "processing now", sources in progress with seconds, ETA of the stage and the whole run, counters, "what we reject" and "where vacancies come from" charts, an event feed of the whole run with counters on its filters (found / rejected with a reason / source totals), warnings grouped as "🛠 needs action" (a board is gone, a company has no board, a list was cut by a limit), "🐞 code problems" (a response format changed, exceptions) and "🌐 site failures" (HTTP errors, SSL, timeouts — usually pass by themselves). Buttons: run everything (like `run_daily.bat`), vacancies only, mail only, "⏹ Остановить прогон" |
| **Отчёты** (Reports) | All `reports/jobs_*.html` by date; any opens inside the page, with a link to the Markdown |
| **Отклики** (Applications) | `reports/applications.html`, an "update mail" button and a "＋ Событие" form (applied / stage / rejected / offer / note) — appends a block to `applications_manual.toml` and rebuilds the page from the mail cache at once. The "✅ откликнулся" button in the report opens the form pre-filled; the vacancy link is remembered and later reports mark it "🔁" |
| **Резюме** (CV) | A CV for any vacancy: by link (from a report or any) or by text, full or one page; "why these bullets"; the list of built CVs (see [cv.md](cv.md)). Until there is a result, the form takes the whole width |
| **Письмо** (Cover letter) | A cover letter for a vacancy ("✉️" in the report, a link or pasted text): "short, bulleted" or "full, in paragraphs"; an editable text with a copy button, "why these points", which key skills you lack (see [cv.md](cv.md#cover-letters)) |
| **Подготовка** (Prep) | For a vacancy ("🎤" in the report, a link, or pasted text for a vacancy without a link): tell-me-about-yourself in English, requirements with proving bullets, gaps, stories for behavioural questions, an HR checklist with the levels.fyi median, questions to the company |
| **Резюме vs рынок** (CV vs market) | A local ATS checker over the day's vacancies: market technologies and where they are in your CV, vacancy titles vs your headline, bullet hygiene, conclusions |
| **Навыки** (Skills) | Profile skills with weights and the share of vacancies: on / off, weight (negative is a penalty), your own skills; edits go to `profile_overrides.json` on top of `profile.toml` and apply from the next run |
| **Источники** (Sources) | Every source of the last run: kind, ATS and slug, how many vacancies, error; "empty" and "with error" filters; Telegram channels; freehire queries with name, parameters and counts |

**Skipping stages.** In advance — "без: почты / Telegram / freehire / агрегаторов" checkboxes next to the run
buttons (mail — do not run `applications.py`, the rest are `--no-…` flags for `main.py`; the checkboxes are
remembered in the browser). On the fly — the "⏭ Пропустить этап" button for the freehire, sources, Telegram, CV and
mail stages: the UI writes a request to `state/progress_control.json`, the stage loop checks it between steps
(`progress.skip_requested()`) and stops within a second or two; what was collected goes on, the stage is marked
"skipped by hand at N of M". On the sources stage the button also aborts requests that are already running
(`http.cancel`), so a long career page does not hold the run.

**Stopping a run.** "⏹ Остановить прогон" (`POST /api/runs/current/stop`) ends the running script together with its
child processes and does not start the next script in the queue (in "run everything" — mail, then vacancies).
What this run collected is lost; the reports of the previous run stay.

A run started from the UI is a child process of the server in its own process group: Ctrl+C in the server window
does not stop it, but **restarting or closing the server interrupts a running run** — restart the server when
nothing is running. Only one run at a time — the UI knows it even if the run was started from a terminal or a
scheduler (checked via `state/progress.json` and whether the process is alive). Output goes to `last_run.log`.

Code: `web/app.py` (FastAPI: pages and API), `web/templates/` (Jinja2), `web/static/` (CSS and JS without a build).
API: `GET /api/progress`, `GET /api/progress/stream` (SSE), `POST /api/runs`, `POST /api/runs/current/skip`,
`POST /api/runs/current/stop`, `GET /api/reports`, `GET /api/sources`, `POST /api/cv`, `GET /api/cv`, `POST /api/cover`,
`GET /api/market`, `GET|POST /api/skills`, `POST /api/applications/events`, `POST /api/prep` — described in `/docs`.
