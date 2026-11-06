# Docker

[← README](../README.md)

The same in a container: web UI, runs and a daily scheduled run.

```bash
docker compose up -d --build
```

The UI is at `http://127.0.0.1:8765` (the port is published on the local address only). Stop: `docker compose down`.

- **Data stays on the host.** The whole project folder is mounted at `/app`: `state/`, `reports/`, `cv_out/`,
  settings, `applications_manual.toml`, the Gmail token — the same files as without Docker. The image
  (`Dockerfile`) has only Python dependencies, Chromium for PDF CVs and fonts (Liberation Sans ≈ Arial).
- **Schedule.** `JOBHUNTER_DAILY_AT: "09:00"` in `docker-compose.yml` — the server runs "everything" (mail, then
  vacancies) once a day, like `run_daily.bat`; a run missed because the computer was off is caught up when the
  container starts. The last run date is in `state/scheduler.json`. Do not combine it with a Task Scheduler / cron
  job, or you will get two runs a day.
- **Gmail.** The first Google sign-in is done on the host (`.venv\Scripts\python.exe applications.py`): the
  container has no browser for the sign-in window. The token is saved to `state/gmail_token.json` and is refreshed
  inside the container afterwards. If the token is revoked, sign in on the host once more.
- **PDF CVs for Fit %.** Host paths in `settings.toml` → `[cv].files` are not visible to the container. Put the
  PDFs in the project folder (for example `cv/`) and use a path relative to the project root: `"cv/My_Backend_CV.pdf"`
  — it works both on the host and in the container. Without PDFs matching uses only `cv/base.toml`.
- **One server per port.** If `python -m web` runs on the host, port 8765 is taken — stop one of the two.
- Variables: `JOBHUNTER_CHROME` (Chrome / Chromium path), `JOBHUNTER_HOST` (server address inside the container,
  `0.0.0.0`), `JOBHUNTER_DAILY_AT` (daily run time), `TZ` (schedule time zone, `UTC` by default — set yours in
  `docker-compose.yml`).
