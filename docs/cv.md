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
