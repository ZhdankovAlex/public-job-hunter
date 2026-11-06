import re
from collections import Counter

from . import config, cv_match, geo, outreach, tiers
from .keys import company_key

# приоритет компании из companies.txt (0–3) — звёздочки перед названием
FLAGS = {3: "★★★", 2: "★★", 1: "★", 0: ""}
SOURCE_LINKS = (
    "[Greenhouse](https://www.greenhouse.com), [Lever](https://www.lever.co), "
    "[Ashby](https://www.ashbyhq.com), [Workable](https://www.workable.com), "
    "[Arbeitnow](https://www.arbeitnow.com), [Remote OK](https://remoteok.com), "
    "[Himalayas](https://himalayas.app)"
)


def _was_rejected(job):
    """Убирать ли вакансию в раздел «Ранее отказ» (settings.toml: hide_previous_rejections)."""
    if not geo.S.get("hide_previous_rejections", False):
        return False
    return "отказ" in f"{job.applied_status} {' '.join(job.history_marks)}".lower()


def _blocked(job):
    """Компания в активном процессе (собеседование) — не для «Нового» и топа; отказ и игнор не мешают."""
    return bool(config.APPLIED_BLOCK_RX.search(f"{job.applied_status} {' '.join(job.history_marks)}"))


def sort_key(job):
    """Порядок в приложениях: свежесть, затем балл (без категорий)."""
    ts = job.posted.timestamp() if job.posted else 0
    return (tiers.age_bucket(job), -job.score, job.language_mismatch_warning, -ts)


def section_key(job):
    """Порядок внутри раздела: компании с «Отказ» в откликах — в конце."""
    return (_was_rejected(job), *sort_key(job))


def _cell(text, limit=None):
    text = " ".join(str(text).split()).replace("|", "\\|")
    if limit and len(text) > limit:
        text = text[: limit - 1] + "…"
    return text


def _marks(job):
    marks = []
    if job.is_new_today:
        marks.append("🆕")
    if job.salary_note:
        marks.append(f"💰 {job.salary_note}")
    if getattr(job, "levels_note", ""):
        marks.append(f"📊 [{job.levels_note}]({job.levels_url})")
    if cv_match.summary(job):
        marks.append(f"🧩 {cv_match.summary(job)}")
    if job.language_mismatch_warning:
        marks.append(f"⚠️ Python not primary language ({job.mismatch_lang})")
    if job.location_check and not job.reloc_type:
        marks.append("❓ remote привязан к стране — проверить")
    if job.referral:
        marks.append("🤝 возможна рефка")
    marks += job.lang_marks
    if job.note.upper().startswith("VERIFY"):
        marks.append(f"🔍 {job.note}")
    if job.app_status:
        marks.append(f"📬 {job.app_status}")
    if job.applied_status:
        marks.append(f"ℹ️ в компанию уже был отклик: {job.applied_status}")
    marks += job.history_marks
    marks += _penalty_marks(job)
    if _was_rejected(job):
        marks.append("↓ ранее отказ")
    return "; ".join(marks)


PER_COMPANY_LIMIT = 5
PRIORITY_TITLE = "✅ Remote без привязки к конкретной стране" + (
    f" / явно включает {config.HOME_COUNTRY}" if config.HOME_COUNTRY else "")
TABLE_HEAD = ["| Компания | Вакансия | Локация | Дата | Ссылка | Пометки |", "|---|---|---|---|---|---|"]


def _table(jobs, overflow, max_rows=None):
    """Таблица с лимитом на компанию (и на число строк); лишнее — в overflow[company]."""
    jobs = sorted(jobs, key=section_key)
    by_company = {}
    for j in jobs:
        by_company.setdefault(j.company, []).append(j)
    shown, extra = set(), {}
    for company, items in by_company.items():
        newest = sorted(items, key=lambda j: -(j.posted.timestamp() if j.posted else 0))
        shown.update(id(j) for j in newest[:PER_COMPANY_LIMIT])
        if newest[PER_COMPANY_LIMIT:]:
            extra[company] = len(newest) - PER_COMPANY_LIMIT
            overflow.setdefault(company, []).extend(newest[PER_COMPANY_LIMIT:])
    visible = [j for j in jobs if id(j) in shown]
    cut = 0
    if max_rows and len(visible) > max_rows:  # оставляем самые свежие
        newest = sorted(visible, key=lambda j: -(j.posted.timestamp() if j.posted else 0))
        keep = {id(j) for j in newest[:max_rows]}
        for j in newest[max_rows:]:
            overflow.setdefault(j.company, []).append(j)
        cut = len(visible) - max_rows
        visible = [j for j in visible if id(j) in keep]
    last_row = {j.company: i for i, j in enumerate(visible)}
    lines = list(TABLE_HEAD)
    for i, j in enumerate(visible):
        company = f"{FLAGS[j.score]} {_cell(j.company)}".strip()
        posted = j.posted.date().isoformat() if j.posted else "—"
        lines.append(
            f"| {company} | {_cell(j.title, 90)} | {_cell(j.location or 'Remote', 60)} | "
            f"{posted} | [{j.source}]({j.url}) | {_cell(_marks(j))} |"
        )
        if last_row[j.company] == i and j.company in extra:
            lines.append(f"| | + {extra[j.company]} more at {_cell(j.company)} → see appendix | | | | |")
    if cut:
        lines.append(f"| | + ещё {cut} более старых вакансий → see appendix | | | | |")
    lines.append("")
    return lines


def _relocation_section(reloc, overflow):
    """🌍: подразделы по странам, внутри — сначала подтверждённая релокация."""
    lines = [f"## 🌍 Релокация и remote по странам — {len(reloc)}", ""]
    for country in [*geo.COUNTRIES, geo.OTHER]:
        group = [j for j in reloc if j.reloc_country == country]
        if not group:
            continue
        group.sort(key=lambda j: (_was_rejected(j), geo.TYPE_ORDER.index(j.reloc_type), sort_key(j)))
        lines += [f"### {country} — {len(group)}", ""]
        lines += [
            "| Компания | Вакансия | Страна / город | Тип | Маркер релокации | Зарплата | Ссылка | Пометки |",
            "|---|---|---|---|---|---|---|---|",
        ]
        per_company = Counter()
        hidden = Counter()
        for j in group:
            per_company[j.company] += 1
            if per_company[j.company] > PER_COMPANY_LIMIT:
                hidden[j.company] += 1
                overflow.setdefault(j.company, []).append(j)
                continue
            quote = f"«{_cell(j.reloc_quote)}»" if j.reloc_quote else "—"
            lines.append(
                f"| {_cell(j.company)} | {_cell(j.title, 90)} | {_cell(j.location, 60)} | {j.reloc_type} | "
                f"{quote} | {_cell(j.salary or '—', 30)} | [{j.source}]({j.url}) | {_cell(_marks(j))} |"
            )
        for company, n in hidden.items():
            lines.append(f"| | + {n} more at {_cell(company)} → see appendix | | | | | | |")
        lines.append("")
    return lines


def _status(job):
    if hasattr(job, "channel"):  # Telegram
        return f"❓ проверить вручную: {job.manual_check}" if job.manual_check else job.label
    if job.manual_check:
        return f"❓ проверить вручную: {job.manual_check}"
    if job.reloc_type:
        return f"🌍 {job.reloc_country}: {job.reloc_type}"
    if job.location_check:
        return "❓ remote в стране"
    return f"✅ {config.HOME_COUNTRY}" if config.HOME_RX.search(job.location or "") else "✅ remote"


PENALTY_NAMES = {
    "experience_7": "требуют 7 лет опыта", "experience_8": "требуют 8+ лет опыта", "level": "уровень Staff/Principal",
    "role": "смежная роль", "language": "основной язык не Python",
    "ops_heavy": "по тексту — DevOps/инфраструктура, а не продукт",
}


def _penalty_marks(job):
    return [f"⬇ штраф: {PENALTY_NAMES.get(p, p)}" for p in job.penalties]


SKILL_GAPS_TOP = 15


def skill_gaps(jobs, tg_jobs) -> dict:
    """Каких навыков чаще всего не хватает в подходящих вакансиях (Backend и ML / AI).

    -> {"total": N, "core": [(навык, сколько вакансий)], "nice": [...]}: по своим вакансиям, а не по всему рынку.
    """
    pool = [j for j in [*jobs, *tg_jobs] if j.tier in (tiers.BACKEND, tiers.ML)]
    core, nice = Counter(), Counter()
    for j in pool:
        core.update(set(j.cv_core_missing))
        nice.update(set(j.cv_nice_missing) - set(j.cv_core_missing))
    return {"total": len(pool), "core": core.most_common(SKILL_GAPS_TOP), "nice": nice.most_common(SKILL_GAPS_TOP)}


def _skill_gaps_section(jobs, tg_jobs):
    gaps = skill_gaps(jobs, tg_jobs)
    if not gaps["total"]:
        return []
    total = gaps["total"]
    lines = [f"## 📊 Каких навыков чаще всего не хватает — по {total} подходящим вакансиям (Backend, ML / AI)", "",
             "| Навык | Ключевой (нет в резюме) | Nice-to-have |", "|---|---|---|"]
    nice = dict(gaps["nice"])
    for name, n in gaps["core"]:
        extra = nice.pop(name, 0)
        lines.append(f"| {name} | {n} ({round(100 * n / total)}%) | {f'{extra} ({round(100 * extra / total)}%)' if extra else '—'} |")
    for name, n in list(nice.items())[:5]:
        lines.append(f"| {name} | — | {n} ({round(100 * n / total)}%) |")
    lines += ["", "Ключевой — навык требуется без оговорок, nice-to-have — «будет плюсом». "
              "Считаются и термины вне словаря (Odoo, ERP…), если вакансия упоминает их 3+ раза.", ""]
    return lines


def _applications_block(stats):
    """📬 Отклики: сводка по статусам и кому напомнить о себе (почта + Telegram-истории)."""
    digest = stats.get("applications")
    if not digest:
        return []
    c = digest["counts"]
    lines = [
        "## 📬 Отклики",
        "",
        f"🟢 этапы {c.get('active', 0) + c.get('offer', 0)} · 🟡 ждём ответа {c.get('waiting', 0)} · "
        f"🟠 отказался сам {c.get('declined', 0)} · 🔴 отказ {c.get('rejected', 0)} — "
        "подробно в reports/applications.html",
        "",
    ]
    if digest.get("warning"):
        lines += [f"> ⚠️ **{_cell(digest['warning'])}**", ""]
    if digest["nudge"]:
        lines += ["**Напомнить о себе** (после этапа тишина):", ""]
        lines += [f"- **{_cell(company)}** — {days} дн. без ответа; последнее: {_cell(subject)}"
                  for company, days, subject in digest["nudge"]]
        lines.append("")
    return lines


def _new_section(jobs, tg_jobs):
    """🆕 Вакансии, которых не было в прошлых прогонах (state/seen.json)."""
    new = [j for j in jobs if j.is_new_today and not _blocked(j)]
    new.sort(key=tiers.sort_key)
    new_tg = sorted((j for j in tg_jobs if j.is_new_today and not _blocked(j)), key=tiers.sort_key)
    lines = [f"## 🆕 Новое за сутки — {len(new) + len(new_tg)}", ""]
    if not new and not new_tg:
        return lines + ["Новых вакансий с прошлого запуска нет.", ""]
    lines += [
        "| Балл | Компания | Вакансия | Локация | Статус | Дата | Источник | Ссылка | Не хватает / пометки |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for j in new:
        company = f"{FLAGS[j.score]} {_cell(j.company)}".strip()
        posted = j.posted.date().isoformat() if j.posted else "—"
        marks = [m for m in _marks(j).split("; ") if m and m != "🆕" and not m.startswith("❓")]
        lines.append(
            f"| **{j.relevance}** | {company} | {_cell(j.title, 90)} | {_cell(j.location or 'Remote', 60)} | "
            f"{_status(j)} | {posted} | {_source(j)} | [оригинал]({j.url}) | {_cell('; '.join([_missing(j), *marks]))} |"
        )
    for j in new_tg:
        posted = j.posted.date().isoformat() if j.posted else "—"
        link = f"[Apply]({j.url})" if j.url else f"{j.contact or '—'} · [пост]({j.post_url})"
        lines.append(
            f"| **{j.relevance}** | {_cell(j.company)} | {_cell(j.title, 90)} | {_cell(j.location or '—', 60)} | "
            f"{j.label} | {posted} | {_source(j)} | {link} | {_cell('; '.join([_missing(j), *j.marks]))} |"
        )
    lines.append("")
    return lines


def _source(job):
    """Источники вакансии: freehire, ATS, агрегатор или Telegram-канал."""
    if hasattr(job, "channel"):
        return f"telegram @{job.channel}"
    return " + ".join(job.sources or [job.source])


def _missing(job, limit=6):
    """Недостающие ключевые навыки (для таблиц Markdown-отчёта)."""
    names = list(getattr(job, "cv_core_missing", []))[:limit]
    return ("не хватает: " + ", ".join(names)) if names else "все ключевые навыки есть"


def _skills(job, limit=6):
    """Совпавшие навыки одной строкой (самые весомые первыми)."""
    names = job.matched_skills[:limit]
    more = len(job.matched_skills) - len(names)
    return ", ".join(names) + (f" +{more}" if more > 0 else "")


def _ranked(jobs, tg_jobs):
    """Все вакансии (ATS + Telegram) по убыванию балла; без компаний в активном процессе (собеседование)."""
    rows = [(j, j.url, _status(j), _marks(j)) for j in jobs if not _blocked(j)]
    rows += [
        (j, j.url or j.post_url, _status(j),
         "; ".join([*([f"💰 {j.salary_note}"] if j.salary_note else []),
                    *([f"📊 [{j.levels_note}]({j.levels_url})"] if j.levels_note else []),
                    *([f"🧩 {cv_match.summary(j)}"] if cv_match.summary(j) else []), *j.marks, *_penalty_marks(j)]))
        for j in tg_jobs if not _blocked(j)
    ]
    rows.sort(key=lambda r: tiers.sort_key(r[0]))
    return rows


def _top_section(jobs, tg_jobs):
    """🎯 Топ-N по релевантности резюме (profile.toml), не больше top_per_company на компанию."""
    limit = int(geo.S.get("top_limit", 100))
    per_company_limit = int(geo.S.get("top_per_company", 3))
    picked, per_company = [], Counter()
    for row in _ranked(jobs, tg_jobs):
        key = company_key(row[0].company) or row[0].company
        if per_company[key] >= per_company_limit:
            continue
        per_company[key] += 1
        picked.append(row)
        if len(picked) == limit:
            break
    lines = [f"## 🎯 Топ-{limit} по релевантности резюме — {len(picked)}", ""]
    if not picked:
        return lines + ["Подходящих вакансий нет.", ""]
    lines += [
        "| # | Балл | Компания | Вакансия | Локация | Статус | Дата | Источник | Ссылка | Не хватает | Пометки |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for n, (j, link, status, marks) in enumerate(picked, 1):
        company = f"{FLAGS[getattr(j, 'score', 0)]} {_cell(j.company)}".strip()
        posted = j.posted.date().isoformat() if j.posted else "—"
        marks = "; ".join(m for m in marks.split("; ") if m and not m.startswith("❓"))
        lines.append(
            f"| {n} | **{j.relevance}** | {company} | {_cell(j.title, 80)} | {_cell(j.location or 'Remote', 50)} | "
            f"{status} | {posted} | {_source(j)} | [оригинал]({link}) | {_cell(_missing(j))} | {_cell(marks)} |"
        )
    lines.append("")
    return lines


TOP_REVIEW_LIMIT = 15
TOP_BLOCKING_PENALTIES = {"level", "language", "experience_7", "experience_8"}


def _why(job):
    """Одна строка «почему подходит»: главные совпавшие навыки и условие по локации."""
    location = job.location or ""
    if config.HOME_RX.search(location):
        place = f"работа в стране проживания ({config.HOME_COUNTRY})"
    elif re.search(r"world|global|anywhere", location, re.I):
        place = "remote по миру"
    elif re.search(r"europe|emea|\beu\b", location, re.I):
        place = "remote по Европе"
    else:
        place = "remote без привязки к стране"
    if getattr(job, "reloc_type", "") == geo.RELOCATION:
        place = f"релокация: {job.reloc_country}"
    extras = ["приоритетная компания"] if getattr(job, "score", 0) >= 2 else []
    return ", ".join([*job.matched_skills[:3], place, *extras])


def pick_review(jobs, tg_jobs):
    """Вакансии для «Топа для разбора»: [(job, ссылка)], не больше TOP_REVIEW_LIMIT."""
    def eligible(job):
        return not (job.manual_check or _blocked(job) or TOP_BLOCKING_PENALTIES & set(job.penalties))

    # ✅ remote / домашняя страна либо 🌍 с подтверждённой релокацией
    rows = [(j, j.url) for j in jobs if eligible(j)
            and (j.reloc_type == geo.RELOCATION or (not j.location_check and not j.reloc_type))]
    rows += [(j, j.url or j.post_url) for j in tg_jobs if eligible(j) and j.label.startswith("✅")]
    rows.sort(key=lambda r: tiers.sort_key(r[0]))
    picked, per_company = [], Counter()
    for job, link in rows:
        key = company_key(job.company) or job.company
        if per_company[key] >= 2:
            continue
        per_company[key] += 1
        picked.append((job, link))
        if len(picked) == TOP_REVIEW_LIMIT:
            break
    return picked


def _review_section(jobs, tg_jobs):
    """⭐ До 15 вакансий: локация подтверждена текстом, без штрафов, текст прочитан."""
    picked = pick_review(jobs, tg_jobs)
    lines = [f"## ⭐ Топ для разбора — {len(picked)}", ""]
    if not picked:
        return lines + ["Вакансий, прошедших все проверки, сейчас нет.", ""]
    lines += [
        "| # | Категория | Балл | Компания | Вакансия | Локация | Статус | Дата | Источник | Ссылка | Почему подходит | Соответствие резюме |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for n, (j, link) in enumerate(picked, 1):
        company = f"{FLAGS[getattr(j, 'score', 0)]} {_cell(j.company)}".strip()
        posted = j.posted.date().isoformat() if j.posted else "—"
        lines.append(
            f"| {n} | {tiers.NAMES[j.tier]} | **{j.relevance}** | {company} | {_cell(j.title, 80)} | "
            f"{_cell(j.location or 'Remote', 50)} | "
            f"{_status(j)} | {posted} | {_source(j)} | [оригинал]({link}) | {_cell(_why(j))} | "
            f"{_cell(cv_match.summary(j))} |"
        )
    lines.append("")
    return lines + _outreach_section(picked)


def _outreach_section(picked):
    """✉️ Кому написать по вакансиям топа: поиск людей в LinkedIn и черновик сообщения.

    Ссылки только собираются, в LinkedIn скрипт не ходит; отправляет пользователь сам.
    """
    lines = ["### ✉️ Написать живому человеку", "",
             "Ссылки открывают поиск людей в LinkedIn; черновик — заметка к запросу на контакт (до 300 символов).", ""]
    for n, (j, _) in enumerate(picked, 1):
        links = [f"[{label}]({url})" for label, url in outreach.search_links(j.company)]
        if outreach.telegram_contact(j):
            links.insert(0, f"[автор поста в Telegram]({outreach.telegram_contact(j)})")
        text = getattr(j, "description", "") or getattr(j, "text", "")
        direct = [f"[{label}]({href})" for _, label, href in outreach.contacts(text, getattr(j, "channel", ""))]
        lines += [f"{n}. **{_cell(j.company) or '—'}** — {_cell(j.title, 80)}: {' · '.join(links) or 'компания не указана'}",
                  *([f"   📇 контакты из вакансии: {' · '.join(direct)}"] if direct else []),
                  f"   > ✉️ рекрутеру: {outreach.draft(j)}",
                  f"   >",
                  f"   > 🤝 инженеру, реферал: {outreach.referral_draft(j)}", ""]
    return lines


def _manual_section(jobs):
    """❓ Вакансии, которые нельзя подтвердить по тексту — в топ не попадают."""
    lines = [f"## ❓ Проверить вручную — {len(jobs)}", ""]
    lines += ["| Балл | Компания | Вакансия | Локация | Почему ❓ | Дата | Ссылка | Пометки |", "|---|---|---|---|---|---|---|---|"]
    for j in sorted(jobs, key=tiers.sort_key):
        posted = j.posted.date().isoformat() if j.posted else "—"
        lines.append(
            f"| {j.relevance} | {_cell(j.company)} | {_cell(j.title, 80)} | {_cell(j.location or 'Remote', 50)} | "
            f"{j.manual_check} | {posted} | [{_source(j)}]({j.url}) | {_cell(_marks(j))} |"
        )
    lines.append("")
    return lines


REFUSED_PER_COMPANY = 4


def _previously_rejected_section(jobs, tg_jobs, filtered):
    """Компании со статусом «Отказ» в откликах — отдельно, вне основных разделов и топа.

    filtered — их вакансии, которые к тому же не прошли фильтры (причина в последней колонке).
    """
    rows = [(j, j.url, "") for j in jobs] + [(j, j.url or j.post_url, "") for j in tg_jobs]
    rows += [(j, j.url or getattr(j, "post_url", ""), j.reject) for j in filtered]
    rows.sort(key=lambda r: (r[0].company.lower(), bool(r[2]), -r[0].relevance))
    lines = [f"## Ранее отказ — {len({r[0].company for r in rows})} компаний, {len(rows)} вакансий", ""]
    lines += [
        "| Компания | Вакансия | Локация | Дата | Ссылка | Отклики | Фильтры |",
        "|---|---|---|---|---|---|---|",
    ]
    per_company = Counter()
    for j, link, reason in rows:
        per_company[j.company] += 1
        if per_company[j.company] > REFUSED_PER_COMPANY:
            continue
        posted = j.posted.date().isoformat() if j.posted else "—"
        history = "; ".join(filter(None, [j.applied_status, *j.history_marks]))
        lines.append(
            f"| {_cell(j.company)} | {_cell(j.title, 80)} | {_cell(j.location or 'Remote', 50)} | {posted} | "
            f"[{_source(j)}]({link}) | {_cell(history)} | {('не прошла: ' + reason) if reason else 'прошла'} |"
        )
    hidden = sum(max(0, n - REFUSED_PER_COMPANY) for n in per_company.values())
    if hidden:
        lines.append(f"| | + ещё {hidden} вакансий этих компаний (не больше {REFUSED_PER_COMPANY} на компанию) | | | | | |")
    lines.append("")
    return lines


REJECTED_LIMIT = 50
REJECTED_PER_BULK_REASON = 3  # «по метке источника» и «старше N дней» — массовые причины


def pick_rejected(rejected):
    """До REJECTED_LIMIT отброшенных вакансий: сначала правила по тексту, затем массовые причины."""
    def is_bulk(job):
        return job.reject.startswith(("локация по метке", "старше"))

    # rejected уже отсортированы по исходному баллу; правила по тексту показываем первыми
    bulk_rows, bulk = [], Counter()
    for j in filter(is_bulk, rejected):
        if bulk[j.reject] < REJECTED_PER_BULK_REASON:
            bulk[j.reject] += 1
            bulk_rows.append(j)
    # отказы по локации из текста — самое важное правило, они идут первыми
    by_text = sorted(
        (j for j in rejected if not is_bulk(j)),
        key=lambda j: (not j.reject.startswith(("on-site", "привязка")), -j.relevance),
    )
    return by_text[: REJECTED_LIMIT - len(bulk_rows)] + bulk_rows


def _rejected_section(rejected):
    """Вакансии подходящей роли, скрытые фильтрами: причина и цитата из текста."""
    shown = pick_rejected(rejected)
    by_reason = Counter(re.sub(r"\d+", "N", j.reject) for j in rejected)
    lines = [f"## Отброшено фильтрами — {len(rejected)} (показано {len(shown)})", ""]
    lines += ["По причинам: " + "; ".join(f"{k} — {v}" for k, v in by_reason.most_common()), ""]
    lines += ["| Исх. балл | Компания | Вакансия | Причина | Цитата | Ссылка |", "|---|---|---|---|---|---|"]
    for j in shown:
        link = j.url or getattr(j, "post_url", "")
        quote = f"«{_cell(j.reject_quote, 110)}»" if j.reject_quote else "—"
        lines.append(
            f"| {j.relevance} | {_cell(j.company)} | {_cell(j.title, 80)} | {j.reject} | {quote} | "
            f"[{_source(j)}]({link}) |"
        )
    lines.append("")
    return lines


def _tg_sort_key(job):
    ts = job.posted.timestamp() if job.posted else 0
    return (_was_rejected(job), not job.label.startswith("✅"), -ts)


def _telegram_section(tg_jobs, stats):
    lines = [f"## Telegram-каналы — {len(tg_jobs)}", ""]
    for channel in stats.get("tg_failed", []):
        lines += [f"> ⚠️ Канал @{channel} прочитать не удалось (см. errors.log).", ""]
    if not tg_jobs:
        return lines + ["Подходящих вакансий нет.", ""]
    lines += [
        "| Канал | Дата | Должность | Компания | Локация / формат | Зарплата | Ссылка или контакт | Пометки |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for j in sorted(tg_jobs, key=_tg_sort_key):
        parts = [f"[Apply]({j.url})"] if j.url else []
        if j.contact or not j.url:
            parts.append(j.contact or "контакт не найден")
        link = " · ".join([*parts, f"[пост]({j.post_url})"])
        marks = [j.label, *j.marks]
        if j.applied_status:
            marks.append(f"ℹ️ в компанию уже был отклик: {j.applied_status}")
        marks += j.history_marks
        if _was_rejected(j):
            marks.append("↓ ранее отказ")
        posted = j.posted.date().isoformat() if j.posted else "—"
        lines.append(
            f"| @{j.channel} | {posted} | {_cell(j.title, 90)} | {_cell(j.company, 40)} | "
            f"{_cell(j.location or '—', 60)} | {_cell(j.salary or '—', 30)} | {link} | {_cell('; '.join(marks))} |"
        )
    lines.append("")
    return lines


def _compact_appendix(title, groups, link_of, suffix_of=lambda j: ""):
    """Компактный список по компаниям: заголовок — локация — ссылка."""
    lines = [f"## {title}", ""]
    for company in sorted(groups, key=str.lower):
        lines += [f"**{_cell(company)}**" + suffix_of(groups[company][0]), ""]
        for j in groups[company]:
            lines.append(f"- {_cell(j.title, 90)} — {_cell(j.location or 'Remote', 60)} — [link]({link_of(j)})")
        lines.append("")
    return lines


def render(day, jobs, stats, tg_jobs=None):
    all_jobs = sorted(jobs, key=sort_key)
    all_tg = tg_jobs or []
    # 🚫 нужен местный язык — убираем из всех разделов в отдельное приложение
    lang_blocked = [j for j in [*all_jobs, *all_tg] if j.lang_required]
    all_jobs = [j for j in all_jobs if not j.lang_required]
    all_tg = [j for j in all_tg if not j.lang_required]
    staffing = [j for j in all_jobs if j.staffing] + [j for j in all_tg if j.staffing]
    all_jobs = [j for j in all_jobs if not j.staffing]
    all_tg = [j for j in all_tg if not j.staffing]
    # компании со статусом «Отказ» — отдельный раздел в конце, не в основных и не в топе
    refused = [j for j in all_jobs if _was_rejected(j)]
    refused_tg = [j for j in all_tg if _was_rejected(j)]
    jobs = [j for j in all_jobs if not _was_rejected(j)]
    tg_main = [j for j in all_tg if not _was_rejected(j)]
    manual = [j for j in jobs if j.manual_check]
    # всё, что прошло фильтр по локации без пометки ❓: домашняя страна,
    # Worldwide/Global/EMEA/Europe либо просто "Remote" без региона
    reloc = [j for j in jobs if j.reloc_type]
    priority = [j for j in jobs if not j.location_check and not j.reloc_type and not j.manual_check]
    rest = [j for j in jobs if j.location_check and not j.reloc_type]
    overflow = {}

    lines = [f"# Вакансии за {day.isoformat()}", ""]
    if not jobs:
        lines += ["Новых подходящих вакансий нет.", ""]

    lines += _applications_block(stats)
    lines += _new_section(jobs, tg_main)
    lines += _review_section(jobs, tg_main)
    lines += _skill_gaps_section(jobs, tg_main)
    lines += _top_section(jobs, tg_main)
    lines += ["---", "", "# Appendix: все актуальные вакансии", ""]

    if priority:
        shown = min(len(priority), config.PRIORITY_MAX_ROWS)
        lines += [f"## {PRIORITY_TITLE} — {len(priority)} (показано до {shown} самых свежих)", ""]
        lines += _table(priority, overflow, config.PRIORITY_MAX_ROWS)

    if manual:
        lines += _manual_section(manual)

    if reloc:
        lines += _relocation_section(reloc, overflow)

    for score in (3, 2, 1, 0):
        group = [j for j in rest if j.score == score]
        if not group:
            continue
        title = f"{FLAGS[score]} Приоритет компании {score}".strip()
        lines += [f"## {title} — {len(group)}", ""]
        lines += _table(group, overflow)

    if tg_jobs is not None:
        lines += _telegram_section(tg_main, stats)

    if overflow:
        lines += _compact_appendix("Appendix: remaining vacancies by company", overflow, lambda j: j.url)

    if staffing:
        groups = {}
        for j in staffing:
            groups.setdefault(j.company, []).append(j)
        lines += _compact_appendix(
            f"Appendix: Staffing/outsourcing — {len(staffing)}", groups,
            lambda j: j.url or j.post_url, lambda j: f" ({j.staffing})",
        )

    if lang_blocked:
        groups = {}
        for j in lang_blocked:
            groups.setdefault(j.company, []).append(j)
        lines += _compact_appendix(
            f"Appendix: Требуется местный язык — {len(lang_blocked)}", groups,
            lambda j: j.url or j.post_url,
        )

    all_rejected = stats.get("rejected", [])
    refused_filtered = [j for j in all_rejected if _was_rejected(j)]
    filtered_out = [j for j in all_rejected if not _was_rejected(j)]
    if refused or refused_tg or refused_filtered:
        lines += _previously_rejected_section(refused, refused_tg, refused_filtered)

    if filtered_out:
        lines += _rejected_section(filtered_out)

    by_score = Counter(j.score for j in jobs)
    by_country = Counter(j.reloc_country for j in reloc)
    confirmed = Counter(j.reloc_country for j in reloc if j.reloc_type == geo.RELOCATION)
    referral_lines = []
    must_lines = []
    for name, found, passed, reason in stats.get("must_report", []):
        if reason:
            must_lines.append(f"- {name}: ATS-борд не найден, см. errors.log (причина: {reason})")
        else:
            must_lines.append(
                f"- {name}: найдено вакансий на борде — {found}, прошло фильтры — {passed}"
                + ("" if passed else " (Python-вакансий сейчас нет)")
            )
    reported = {company_key(row[0]) for row in stats.get("must_report", [])}
    for name, has_board in stats.get("referral", []):
        if company_key(name) in reported:
            continue
        if not any(company_key(j.company) == company_key(name) for j in [*jobs, *tg_main]):
            referral_lines.append(
                f"- {name}: Python-вакансий сейчас нет"
                + ("" if has_board else " (ATS-борд не найден — проверьте карьерный сайт вручную)")
            )
    lines += [
        "## Сводка",
        "",
        f"- Всего найдено (прошли фильтр): **{stats['matched']}**",
        f"- Уже есть отклик (скрыто): {stats['in_applied']}",
        f"- Дублей объединено (одна вакансия в нескольких локациях): {stats.get('merged', 0)}",
        *(
            [f"- freehire: получено {fh['fetched']} (запросов в сеть {fh['requests']}, из кэша {fh['cache_hits']}; "
             f"по запросам: " + ", ".join(f"{k} — {v}" for k, v in fh['totals'].items()) + "), "
             f"добавлено новых {fh['added']}, совпало с другими источниками {fh['twins']}, "
             f"полных текстов получено {fh['described']} из {fh.get('candidates', 0)}, "
             f"не прошло проверку {fh['dropped']}",
             *(f"- freehire, запрос {name} вернул 0: {text}" for name, text in fh.get("diagnosis", {}).items())]
            if (fh := stats.get("freehire")) and fh["enabled"] else []
        ),
        f"- Ранее отказ (отдельный раздел): {len(refused) + len(refused_tg) + len(refused_filtered)}",
        f"- ❓ Проверить вручную: {len(manual)}",
        f"- Отброшено фильтрами (подходящая роль): {len(filtered_out)} — полный список в state/rejected_latest.tsv",
        *(
            [f"- База зарплатных вилок: {sal['samples']} вакансий с указанной зарплатой (EUR/мес, медианы). "
             + " | ".join(sal["summary"])]
            if (sal := stats.get("salary")) and sal["samples"] else []
        ),
        f"- Отброшено как старше {stats.get('max_age_days', 30)} дней: {stats.get('stale', 0)}",
        f"- Вынесено в Staffing/outsourcing: {len(staffing)}",
        f"- В основном отчёте: **{len(jobs)}**, из них впервые увидены сегодня: "
        f"{sum(j.is_new_today for j in jobs)}",
        f"- {PRIORITY_TITLE} (подтверждено текстом): **{len(priority)}** вакансий",
        f"- В разделе 🌍: **{len(reloc)}** вакансий"
        + (" — " + ", ".join(
            f"{c}: {n} (подтв. релокация: {confirmed.get(c, 0)})" for c, n in by_country.items()
        ) if reloc else ""),
        f"- Remote в стране, допускающий работу вне её: "
        f"{sum(j.reloc_type == geo.REMOTE_OK for j in reloc)}",
        f"- Отброшено по языку (🚫 нужен местный язык): {len(lang_blocked)}",
        *must_lines,
        *referral_lines,
        "- По приоритету компании: " + ", ".join(f"{s} → {by_score.get(s, 0)}" for s in (3, 2, 1, 0)),
        f"- С пометкой ⚠️ language mismatch: {sum(j.language_mismatch_warning for j in jobs)}",
        *(
            [f"- Telegram-каналы: {len(tg_main)} вакансий, из них ✅ — "
             f"{sum(j.label.startswith('✅') for j in tg_main)}"]
            if tg_jobs is not None else []
        ),
        f"- Компаний с найденным ATS-бордом: {stats['mapped']} из {stats['companies']} "
        f"(остальные — в errors.log)",
        f"- Просмотрено вакансий до фильтра: {stats['fetched']}",
        "",
        f"Источники: {SOURCE_LINKS}.",
        "",
    ]
    return "\n".join(lines)
