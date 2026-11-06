# Order, score, skills, Fit, salaries

[← README](../README.md)

## Report layout

1. `📬 Отклики` — application counters and who to follow up with.
2. `🆕 Новое за сутки` — vacancies seen for the first time today (`state/seen.json` keeps links between runs;
   a second run on the same day does not reset the section). Companies where an interview is in progress are
   not shown here or in the tops.
3. `⭐ Топ для разбора` — up to 15 vacancies with a ✅ status, read full text and no blocking penalties, with
   outreach links and message drafts.
4. `📊` missing skills — which skills your CV lacks most often across matching vacancies.
5. `🎯 Топ-N по релевантности` — the best by score (`top_limit`, `top_per_company`).
6. Appendix: ✅, ❓, 🌍 by country, groups by company priority, Telegram, staffing, local language, rejected.

The HTML report (`reports/latest.html`) is one table with the same data, filters (new, top, ✅, Fit ≥ 80%,
priority companies, salary, relocation, check), sorting and a search box.

## Order: categories

Vacancies are sorted by category first, then by freshness within the category (up to 3 days, a week, two
weeks, a month, older; `AGE_BUCKETS`), and only then by score (`jobhunter/tiers.py`): a fresh vacancy with
skill gaps goes above an old perfect match — a fresh one has a better chance of an invitation. A vacancy
without a date counts as "up to a month". Right after the category comes money: a vacancy whose stated range
is entirely below `[salary] min_monthly_eur` goes to the end of its category (0 — no threshold).

Categories: 1) Backend, 2) ML / AI, 3) DevOps / platform (including roles that are operations by text),
4) other — full-stack, frontend, data / analytics and vacancies whose key stack is not in the CV.

## Relevance score

`profile.toml` has the skills from your CV (name, weight, pattern) and adjustments in `[scoring]`: Python /
Backend / PostgreSQL in the title, product vs platform, company priority from `companies.txt`
(`company_priority_per_point`), location, primary language, experience, Fit. Skills and weights can also be
changed on the "Навыки" page of the web UI (saved to `profile_overrides.json` on top of `profile.toml`).

## CV matching

`jobhunter/cv_match.py` reads your CVs from `settings.toml` (`[cv].files`, PDF; without them — `cv/base.toml`)
and compares them with the vacancy text using a technology dictionary. Each vacancy gets "✔ full match" or
"missing: …" (required technologies not in the CV; nice-to-have and alternatives like "X, Y or Z" do not count),
"mismatch: …" (experience, English level, degree, another primary language) and which CV to send (the first
one for AI / ML roles). A skill counts without the exact word if the CV has something implying it (`CV_ALIASES`):
for example FastAPI → Pydantic, S3 → AWS (partially).

### Fit %

`cv_match.fit_percent`: dictionary skills are **key** (title and normal requirements) or **nice-to-have** (only
next to "nice to have", "a plus", "preferred"); a skill required anywhere without a caveat is key. Alternatives
("Python, Go or Java") do not penalise if you have one of them. Fit = 85% × share of covered key skills +
15% × share of covered nice-to-have; a key skill weighs by the number of mentions (1–10). Terms outside the
dictionary also count: a capitalised word in the middle of a sentence that appears 3+ times or is in the title
is a key skill (except the company, generic words from `_GENERIC_TERMS` and what your CV has). Minus 15 for each
mismatch and 3 for a partial match. In HTML — a "Fit %" bar (green from 85, light green from 70, yellow from 50,
red below; hover for the breakdown) and the "Fit ≥ 80%" filter.

## Salary ranges

`jobhunter/salary.py` keeps a base of vacancies with an explicit salary (`state/salary_samples.json`, 180 days)
and converts everything to EUR per month (rates in `[salary.rates_to_eur]`). Samples are grouped into the
global market and Russian-language Telegram channels (`[telegram].cis_channels`). Every vacancy shows either the
stated range or an estimate: the interquartile range of the same role and grade. It is a market reference, not a
company's offer.

Grade (`salary.grade`): from the title (junior / middle / senior / lead), otherwise from the required experience:
up to 1 year — junior, 2–3 — middle, 4–7 — senior, 8+ — lead.

### levels.fyi: country median

The second line in the salary column (HTML) and the "📊" mark (Markdown): "levels.fyi, Germany: median ≈
€5.9K/month, 25–75%: €4.6K–7.2K", with a link (`jobhunter/levels.py`).

- The country comes from the vacancy location (`levels.LOCATIONS`). "Remote Europe / EU / CET" — Europe as a
  whole; plain remote / worldwide — your home country (`[home]`), or Europe if it is not in the table: remote
  pay usually depends on the worker's country.
- Data: Software Engineer, all grades, total compensation (salary + bonus + stock) reported by people.
- The source is the Markdown pages levels.fyi serves to agents (`…/locations/<country>.md`): by their
  robots.txt they may be read and quoted with a link, not used to train models. Requests go through
  `http.py` (robots.txt, 1 request/s), cached in `state/levels_cache.json` for 7 days.

## Applications in the report

- `## 📬 Отклики` at the top: 🟢 / 🟡 / 🟠 / 🔴 counters and "follow up" — stages followed by 5+ days of
  silence. Vacancies of companies you applied to get a "📬 status, date" mark. The source is the local mail
  cache and `applications_manual.toml` (no Gmail requests); `run_daily.bat` runs `applications.py` first.
