# A CV for a vacancy

[← README](../README.md)

The base is `cv/base.toml`: contacts, education, publications, languages (unchanged), a pool of achievements
for the summary, skills and experience bullets with topics (`topics`). The generator invents nothing — it only
selects and orders facts from the base for the vacancy (`jobhunter/cvgen.py`). Start from
`cv/base.example.toml`: every field is commented there.

## How bullets are picked

Score of every achievement and experience bullet:

- a vacancy **technology** in the bullet (dictionary `cv_match.TECH_RX`) — 2 points each;
- a vacancy **topic** shared with the bullet (`TOPICS`: performance, security, highload, …) — 1 point;
- **text similarity** of the bullet to the vacancy — up to 4 points: TF-IDF cosine over crudely stemmed words
  (scaling / scaled → scal), IDF over the base bullets, so words present everywhere in the CV weigh less.

Then: `pin` — always; the rest by score, from 5 to `bullets_max`. The headline is the AI variant for AI / LLM
roles, otherwise backend; summary = intro + 2–3 best achievements + closing; skills — what the vacancy has plus
`always` (at least 3 per group), the AI / LLM group only for AI roles. The top of the `.md` has a comment: the
vacancy, Fit %, which key skills are missing, technologies and topics.

Real embeddings (sentence-transformers) are not used: ~2 GB of dependencies (PyTorch) for a few dozen bullets.

## One-page version

`one_page` / `--one-page`: denser layout, one company description, 2 achievements in the summary, no
Publications section and thesis topic. After the build the PDF pages are counted (pypdf); if more than one,
it shrinks step by step (`Limits.shrink`): first the skill groups least matching the vacancy (down to 5), then
experience bullets (the latest job down to 3, the previous one down to 1, older ones down to 1), then the
summary — until it fits. The file is `…_1p.pdf`.

## How to build

- **"📄 CV" / "1 стр." buttons in the HTML report** next to every vacancy: open the CV page of the web UI
  (needs `python -m web`) and build at once. The vacancy is looked up by link in `state/jobs/<report date>.json`,
  so it works for old reports too.
- **The CV page** (`/cv`): a link to any vacancy or pasted text. A link not in the reports is read from the page
  (`jobhunter/vacancy_lookup.py`: robots.txt, 1 request/s; the text comes from `JobPosting` markup, else the whole
  page). For LinkedIn and pages that render the vacancy with scripts, paste the text. Next to the result: "why
  these bullets" (score, technologies, topics, similarity) and which key skills the CV lacks.
- **API:** `POST /api/cv` (`url`, `date`, `text`, `company`, `title`, `one_page`), `GET /api/cv`.
- **Command line:**

```
python cvgen.py <link or part of the title>                  # a vacancy from the last report
python cvgen.py --text vacancy.txt --company X --title "…"   # from the vacancy text
python cvgen.py <…> --one-page                               # one-page version
python cvgen.py --render cv_out/<date>/<file>.md             # rebuild the PDF after editing
```

The result is `cv_out/<date>/<company>_<vacancy>[_1p].md`, `.html` and `.pdf` (headless Chrome; the Chrome path
is `settings.toml` → `[cv].chrome` or the `JOBHUNTER_CHROME` variable). Read it before sending; edit the `.md` and
`--render`. Facts change only in `cv/base.toml`.

Base CVs without a vacancy: `python cvgen.py --base backend` and `--base ai` →
`cv_out/<First_Last>_Backend_CV.pdf` and `<First_Last>_Backend_AI_CV.pdf` (the name comes from `name` in
`cv/base.toml`). The default style is classic (Arial, blue headings; `settings.toml` → `[cv].style`),
`--style harvard` uses a serif font.

Automatic CVs during a run (`cvgen.auto_build`: new vacancies with Fit ≥ `auto_fit_min`) are **off**
(`[cv].auto_max = 0`): CVs are built by the button for any vacancy.

## Cover letters

`jobhunter/cover_letter.py`, the "Письмо" page of the web UI (`/cover`, API `POST /api/cover`) and the "✉️" button next
to a vacancy in the report. Like the CV, the letter invents nothing: achievements from `[summary]` and soft skills from
`[cover]` in `cv/base.toml` are ranked against the vacancy (technologies, topics, text similarity) and put into a
template: who you are → what you bring (hard) → how you work (soft) → honestly, what you have not used → a call to talk.

- **Short** (`short`) — a one-line intro, a bulleted "what I can bring", the stack overlap, a call to talk.
- **Full** (`full`) — paragraphs: intro, achievements (as a list — CV bullets are written without "I"), stack and soft
  skills, which key skills of the vacancy you have not used in production.

The template is the `[cover]` section of `cv/base.toml`; `{company}` and `{title}` are taken from the vacancy:

```toml
[cover]
greeting = "Hi {company} team,"
intro = "I'm a Backend Engineer with 5 years of Python and PostgreSQL."
opening = "Your {title} role matches what I've been doing, so I'd like to apply."
missing = "I haven't worked with {missing} in production yet, and I'm happy to ramp up on it."
closing = "I'd be glad to have a call and discuss the role in more detail."

[[cover.soft]]
text = "I own features end to end, from design to production support."
topics = ["architecture"]
```

Without `[cover]` neutral wording is used. The letter is saved next to the CV:
`cv_out/<date>/<company>_<vacancy>_cover_<style>.txt`. Read and edit it before sending — the tool sends nothing.
