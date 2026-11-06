# Interview preparation

[← README](../README.md)

The "Подготовка" page of the web UI (`/prep`, API `POST /api/prep`), code — `jobhunter/interview_prep.py`.
It opens from the "🎤" button next to a vacancy in the report or by a vacancy link. Everything is built from the
facts in `cv/base.toml` — nothing is invented:

- vacancy requirements that are in your experience, and the experience bullet that proves each;
- requirements that are not in your experience (say so honestly and show something close);
- a draft "tell me about yourself" in English: current role → the company in one sentence → tasks in the
  vacancy's words → matching stack → earlier experience, shorter;
- stories from the `[[stories]]` bank in `cv/base.toml` for typical behavioural questions (initiative,
  early_detection, pushback, research, incident, ownership), matched to the vacancy;
- an HR call checklist (salary — with the levels.fyi median for the vacancy country, see [scoring.md](scoring.md))
  and questions to ask the company.

Stories with `draft = true` are drafts: check the wording and add details the base does not have.
