# Filters and full-text checks

[← README](../README.md)

## Filters

- **Keywords**: python / django / flask / fastapi / postgres(ql) / backend in the title or the description.
  The title must also be an engineering role and must not be frontend / mobile / QA / intern / junior, etc.
  (`TITLE_ROLE_RX`, `TITLE_EXCLUDE_RX` in `config.py`).
- **Location**: the home country (`[home]` in `settings.toml`), remote with a worldwide / EMEA / Europe marker,
  or plain "Remote" without a region. Remote only in US / Canada / APAC / LATAM is dropped. Remote bound to a
  specific European country is shown with `❓` (if `country_bound_remote = true`).
- **⚠️ Python not primary language**: another language in the title, or the description mentions it much more
  often than Python, or Python appears only in a nice-to-have context.
- **Full-time only** (employment or contract): part-time, freelance, hourly pay, internships and
  "AI Trainer" are dropped (`NOT_FULL_TIME_RX`).
- **Freshness**: `max_age_days` in `settings.toml`; without a publication date the age counts from the first
  time the vacancy was seen (`state/seen.json`). `priority_companies` ignore this window.

## Full-text checks

`jobhunter/verify.py` — a source's location label cannot be trusted ("Remote" with "two days a week in our
London office" in the text), so the status is set by the text:

- **Hidden** (the "Отброшено фильтрами" section, with the reason and a quote): a mandatory office outside the
  home country ("in-person", "on-site", "in our … office", "N days in the office", a city as the workplace),
  "must be based in / right to work in" another country, primary language Go / Java / Kotlin / Ruby / PHP /
  .NET / Rust, "Spring Boot" / "JVM", "AI trainer / per task / annotator".
- **❓ Check by hand**: the text is not available (the source gives only the title), or the label is just
  "Remote" and the text does not confirm work from anywhere. These never get into the top.
- **✅**: Worldwide / Global / Anywhere / Europe / EMEA / EU or the home country — and no stop phrases.
- **Score penalties**: 7 years of experience, 8+ years, Staff / Principal, an adjacent role (Data / Analytics
  Engineer, Data Scientist, Research, DevOps, SRE, Security, Infrastructure without Backend / Software in the
  title). Weights are in `profile.toml`.
- **Russia-only remote** (`[filters].hide_russia_only`, off by default): hides vacancies where remote work is
  possible only from Russia — the "US only" of Russian-language sources.

Companies and vacancies with a "rejected" / "ignored" / "declined" status in your applications are shown as
usual: you can apply again. A vacancy you already applied to is marked "🔁". Only vacancies whose application
is still in progress are hidden. `hide_previous_rejections = true` moves companies that rejected you to a
separate "Ранее отказ" section. The full list of rejected vacancies is `state/rejected_latest.tsv`.

## Default profile: product backend, not DevOps

DevOps / SRE / Infrastructure / Security / Cloud / Systems Engineer roles (without Backend or Software Engineer
in the title) are shown only with Python in the text and go to the third category (`OPS_ROLE_RX`). If the title
says developer but the text is mostly Terraform, Kubernetes, on-call and incidents, the `ops_heavy` penalty
applies and the vacancy does not get into the top. Change the regexes in `config.py` and the weights in
`profile.toml` for another profile.

## Home country, relocation, language, staffing

Location status (by priority):

1. `✅` — remote without a country binding, or the location includes the home country.
2. `🌍` — the location is in a `relocation_countries` country:
   - "релокация" (relocation) — the text has a marker (relocation, visa sponsorship, work permit, Blue Card…),
     quoted in the report; negated phrases ("no visa sponsorship") do not count;
   - "релокация (не подтверждена)" (unconfirmed) — no marker, but the company is in `[relocation_companies]`;
   - for remote within a country: "allows work from outside" / "unclear" / "❌ residents only".
   With `any_country = true` a vacancy in any other country also goes to 🌍 ("Other"), but only with a
   relocation-support marker. An office in another country with relocation support in the text is 🌍, not a
   rejection.
3. `❓` — remote bound to another country.

Language filter (`jobhunter/language.py`): `🚫 local language required` moves the vacancy to the
"Требуется местный язык" appendix; "local language is a plus" and "vacancy not in English" are only marks.

Staffing (`staffing_blacklist.txt`): staffing agencies and marketplaces (names and `re:` patterns), vacancies
with "Contract" in the title, and texts that mention outstaffing / outsourcing / "for one of our clients" go to the
"Staffing/outsourcing" appendix.

The same vacancy (company + title) in several locations is merged into one row.
