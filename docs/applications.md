# Applications: mail and manual events

[← README](../README.md)

## Statuses from mail

`applications.py` reads your recruiter correspondence in Gmail and builds `reports/applications.html`: for every
company — the current status (📨 applied / 🗓️ stages / ❌ rejected / 🎉 offer), how many stages, the first and
last letter dates, days of silence, the history and a link to the last letter.

- If the token has expired or was revoked, `applications.py` opens the Google sign-in window and waits 3 minutes.
  If sign-in fails or Gmail is unavailable, the page is built from the cache, the reason goes to
  `state/mail_sync.json`, and a red ⚠️ warning appears on the page and in the vacancy report; the same happens if
  mail has not been updated for more than a day. In Google Cloud "Testing" mode a token lives 7 days.
- Mail access is **read-only** (`gmail.readonly`): letters are not sent, labelled or deleted. At most 1 request
  per second.
- Letters are parsed by patterns (`jobhunter/mail.py`): the company comes from the ATS sender name (Greenhouse,
  Lever, Ashby, Workable…), the subject ("Thank you for applying to X", "… at X"), job site texts or the corporate
  domain (`anna@acme.com` → Acme). Letter type: offer > rejection > stage / application in the subject > a real
  invitation in the text (a call link, Calendly, "please share your availability") > application confirmation >
  description of stages. Only the letter's own text is parsed: quoted history ("On … wrote:", lines with ">") is
  cut off. Conditional phrases ("If you are not selected…", "if your profile fits, we will schedule an interview")
  and disclaimers count neither as a rejection nor as an invitation.
- Final status and row colour (`applications_report.effective_status`):
  - 🟢 green — stages are going (an invitation by mail, or an interview event in `applications_manual.toml`), offer;
  - 🟡 yellow — waiting: you applied, no answer yet, no more than `timeout_days` (14) days passed;
  - 🟠 orange — you declined (a `declined` event);
  - 🔴 red — any rejection: a rejection letter, a `rejected` event, or more than `timeout_days` days of silence
    after an application or a stage.
  A new application by mail after a rejection letter is a new attempt: 🟡 again.
- Applications not from mail (Telegram, LinkedIn, calls) go to `applications_manual.toml`: company, source and
  events (`applied` / `interview` / `rejected` / `offer` / `declined` / `other`) with a date, a note and an optional
  vacancy `url`. If the company is also in the mail, the events are merged into one row. Events with a `url` also
  dedupe the vacancy report: a vacancy you applied to (not rejected) is hidden; after a rejection it is shown with
  a "🔁" mark.
- Where the company comes from, besides the sender and the subject: a Google Calendar invitation (the organiser's
  corporate domain), the letter text ("…position with Acme", "interest in Acme's … role"), iCIMS / Recruitee senders
  ("Acme @ icims"). A LinkedIn InMail titled "JOB OFFER AT …" is a recruiter writing first, not an offer; an offer
  mentioned in the process description of an application confirmation is not an offer either.
- Letters that look like an application / stage / rejection but have no clear company go to "Компания не
  определена" at the bottom. Fix company spelling in `settings.toml` → `[mail.company_aliases]`; for letters where the
  company is nowhere, bind a sender (part of the name or address, also searched in the signature) in
  `[mail.sender_company]`; your own name in senders goes to `[mail].own_names`.
- **Funnel.** Above the table: "Отклики N → скрининг → техничка → финал → оффер" — how many companies got at least to
  each stage, % of applications. The stage is the furthest one by the words in letter subjects and notes (tech /
  live coding / take-home / with the team — tech interview; final / CTO / founder — final; other invitations —
  screening; cancelled meetings and "next: …" plans do not count). The "Дошли до" column shows it per company; links
  open in a new tab.
- The mail cache is `state/mail_cache.json`, the token — `state/gmail_token.json` (`state/` is in `.gitignore`).

`--no-applied` (for both `main.py` and `applications.py`) ignores `applications_manual.toml`.

## One-time Gmail setup

1. https://console.cloud.google.com → create a project (for example `job-hunter`).
2. "APIs & Services" → "Library" → **Gmail API** → Enable.
3. "OAuth consent screen" → type **External** → fill in the name and your email → add the mailbox you use for
   job search to "Test users".
4. "Credentials" → "Create credentials" → **OAuth client ID** → type **Desktop app** → download the JSON and put it
   in the project root as `gmail_client.json` (it is in `.gitignore`).
5. Run `.venv\Scripts\python.exe applications.py` — a Google sign-in window opens: sign in to that mailbox and allow
   reading. The first run reads all letters for a year (≈1 letter per second), later only new ones.
