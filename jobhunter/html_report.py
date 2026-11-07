"""HTML-версия отчёта: одна самодостаточная страница с таблицей, фильтрами и сортировкой."""
from __future__ import annotations

import re
from datetime import date
from html import escape
from urllib.parse import quote

from . import cv_match, outreach, report, salary, tiers

_CSS = """
:root { color-scheme: light dark; --bg:#fff; --fg:#1b1f24; --muted:#667085; --line:#e4e7ec; --row:#f8fafc;
        --accent:#2563eb; --ok:#15803d; --warn:#b45309; --chip:#eef2f6; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#14171c; --fg:#e6e8eb; --muted:#98a2b3; --line:#2b313a; --row:#1b2027; --accent:#7aa7ff;
          --ok:#4ade80; --warn:#fbbf24; --chip:#252b34; } }
* { box-sizing: border-box; }
body { margin: 0; padding: 20px 24px 60px; background: var(--bg); color: var(--fg);
       font: 14px/1.45 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
h1 { font-size: 20px; margin: 0 0 4px; } h2 { font-size: 16px; margin: 0; display: inline; }
.sub { color: var(--muted); margin-bottom: 14px; }
.warning { border: 2px solid #d33; border-radius: 6px; padding: 8px 12px; margin-bottom: 14px; font-weight: bold; }
.bar { position: sticky; top: 0; z-index: 2; background: var(--bg); padding: 10px 0; display: flex; gap: 8px;
       flex-wrap: wrap; align-items: center; border-bottom: 1px solid var(--line); }
.bar button { border: 1px solid var(--line); background: var(--chip); color: var(--fg); padding: 6px 12px;
              border-radius: 999px; cursor: pointer; font: inherit; }
.bar button.on { background: var(--accent); border-color: var(--accent); color: #fff; }
.bar input { flex: 1; min-width: 220px; padding: 7px 12px; border: 1px solid var(--line); border-radius: 8px;
             background: var(--bg); color: var(--fg); font: inherit; }
.count { color: var(--muted); white-space: nowrap; }
.wrap { overflow-x: auto; }
/* основная таблица — своя прокрутка высотой в экран: горизонтальная полоса всегда у нижнего края окна,
   шапка колонок прилипает; длинные ячейки переносятся, чтобы таблица была уже */
.wrap.main-wrap { overflow: auto; max-height: calc(100vh - 64px); border-bottom: 1px solid var(--line); }
.main-wrap table { margin-top: 0; }
/* плавающая горизонтальная полоса у нижнего края окна — двигает таблицу, пока таблица на экране */
.hscroll { position: fixed; bottom: 0; height: 16px; overflow-x: auto; overflow-y: hidden; z-index: 5;
           background: var(--bg); border-top: 1px solid var(--line); }
.hscroll div { height: 1px; }
/* своя горизонтальная полоса таблицы скрыта — её заменяет плавающая .hscroll; вертикальная остаётся */
.main-wrap::-webkit-scrollbar { height: 0; width: 10px; } .main-wrap::-webkit-scrollbar-thumb { background: var(--line); border-radius: 5px; }
.main-wrap thead th { position: sticky; top: 0; z-index: 1; background: var(--bg); }
.main-wrap td.small { min-width: 140px; max-width: 280px; }
table { border-collapse: collapse; width: 100%; margin-top: 8px; }
th, td { text-align: left; padding: 7px 10px; border-bottom: 1px solid var(--line); vertical-align: top; }
th { cursor: pointer; user-select: none; white-space: nowrap; color: var(--muted); font-weight: 600; }
th:hover { color: var(--fg); }
tbody tr:hover { background: var(--row); }
td.n { font-variant-numeric: tabular-nums; font-weight: 700; text-align: right; }
td.small, .small { color: var(--muted); font-size: 12.5px; }
a { color: var(--accent); text-decoration: none; } a:hover { text-decoration: underline; }
.tag { display: inline-block; padding: 1px 7px; border-radius: 999px; background: var(--chip); font-size: 12px;
       margin-right: 4px; white-space: nowrap; }
.tag.new { background: var(--accent); color: #fff; } .tag.top { background: #f59e0b; color: #1b1f24; }
.ok { color: var(--ok); } .warn { color: var(--warn); }
details { margin-top: 26px; border-top: 1px solid var(--line); padding-top: 14px; }
.fit { position: relative; min-width: 74px; height: 20px; border-radius: 5px; background: var(--chip); overflow: hidden; }
.fit i { position: absolute; inset: 0 auto 0 0; display: block; }
.fit b { position: relative; display: block; text-align: center; line-height: 20px; font-size: 12.5px; }
summary { cursor: pointer; }
.reach { margin-top: 4px; font-size: 12.5px; }
button.copy { border: 1px solid var(--line); background: var(--chip); color: var(--fg); cursor: pointer; font: inherit; }
"""

_JS = """
const rows = [...document.querySelectorAll('#main tbody tr')];
const search = document.getElementById('q'), count = document.getElementById('count');
let mode = 'all';
function apply() {
  const q = search.value.trim().toLowerCase(); let shown = 0;
  for (const r of rows) {
    const okMode = mode === 'all' || r.dataset[mode] === '1';
    const ok = okMode && (!q || r.textContent.toLowerCase().includes(q));
    r.hidden = !ok; if (ok) shown++;
  }
  count.textContent = shown + ' из ' + rows.length;
}
document.querySelectorAll('.bar button').forEach(b => b.addEventListener('click', () => {
  mode = b.dataset.mode;
  document.querySelectorAll('.bar button').forEach(x => x.classList.toggle('on', x === b));
  apply();
}));
search.addEventListener('input', apply);
document.querySelectorAll('table').forEach(table => {
  table.querySelectorAll('th').forEach((th, i) => th.addEventListener('click', () => {
    const body = table.tBodies[0], dir = th.dataset.dir === 'asc' ? 'desc' : 'asc';
    th.dataset.dir = dir;
    const key = tr => tr.children[i].dataset.sort ?? tr.children[i].textContent.trim().toLowerCase();
    [...body.rows].sort((a, b) => {
      const x = key(a), y = key(b), nx = parseFloat(x), ny = parseFloat(y);
      const cmp = (!isNaN(nx) && !isNaN(ny)) ? nx - ny : String(x).localeCompare(String(y), 'ru');
      return dir === 'asc' ? cmp : -cmp;
    }).forEach(tr => body.appendChild(tr));
  }));
});
document.querySelectorAll('button.copy').forEach(b => b.addEventListener('click', async () => {
  const text = b.dataset.msg;
  try { await navigator.clipboard.writeText(text); }
  catch (e) {
    const t = document.createElement('textarea'); t.value = text; document.body.appendChild(t);
    t.select(); document.execCommand('copy'); t.remove();
  }
  const label = b.textContent; b.textContent = '✔ скопировано'; setTimeout(() => b.textContent = label, 1500);
}));
// плавающая горизонтальная полоса: родная полоса таблицы часто за краем экрана
(function () {
  const wrap = document.querySelector('.main-wrap');
  if (!wrap) return;
  const bar = document.createElement('div'); bar.className = 'hscroll';
  const inner = document.createElement('div'); bar.appendChild(inner); document.body.appendChild(bar);
  let lock = false;
  bar.addEventListener('scroll', () => { if (lock) { lock = false; return; } lock = true; wrap.scrollLeft = bar.scrollLeft; });
  wrap.addEventListener('scroll', () => { if (lock) { lock = false; return; } lock = true; bar.scrollLeft = wrap.scrollLeft; });
  function place() {
    const r = wrap.getBoundingClientRect();
    const visible = r.top < innerHeight - 20 && r.bottom > 40 && wrap.scrollWidth > wrap.clientWidth;
    bar.style.display = visible ? 'block' : 'none';
    bar.style.left = r.left + 'px'; bar.style.width = wrap.clientWidth + 'px';
    inner.style.width = wrap.scrollWidth + 'px'; bar.scrollLeft = wrap.scrollLeft;
  }
  addEventListener('scroll', place, { passive: true }); addEventListener('resize', place);
  new ResizeObserver(place).observe(wrap); place();
})();
apply();
"""


def _e(text) -> str:
    return escape(str(text or ""))


def _link(job) -> str:
    return job.url or getattr(job, "post_url", "")


def _date(job) -> str:
    return job.posted.date().isoformat() if job.posted else ""


def _linkify(text: str) -> str:
    """Ссылки в уже экранированном тексте пометок делает кликабельными."""
    return re.sub(r"https://t\.me/[\w/]+", lambda m: f'<a href="{m.group(0)}" target="_blank" rel="noopener">{m.group(0)}</a>', text)


WEB_URL = "http://127.0.0.1:8765"  # веб-интерфейс (python -m web): сборка резюме по кнопке


def _cv_link(job, day: date) -> str:
    """Кнопки «📄 CV» и «1 стр.»: страница «Резюме» веб-интерфейса собирает резюме под эту вакансию.

    Ссылка абсолютная — работает и из отчёта, открытого файлом, если запущен python -m web.
    Если резюме уже собрано автосборкой (cvgen.auto_build), рядом ссылка на него.
    """
    link = _link(job)
    if not link:
        return ""
    query = f"url={quote(link, safe='')}&date={day.isoformat()}&auto=1"
    out = (f' <a class="tag cv" href="{WEB_URL}/cv?{_e(query)}" target="_blank" '
           f'title="Собрать резюме под эту вакансию (нужен запущенный python -m web)">📄 CV</a>'
           f'<a class="tag cv" href="{WEB_URL}/cv?{_e(query)}&amp;one_page=1" target="_blank" '
           f'title="Резюме на одну страницу">1 стр.</a>')
    if getattr(job, "cv_file", ""):
        out += f'<a class="tag" href="../{_e(job.cv_file)}" target="_blank" title="собрано автоматически">📄 готовое</a>'
    out += (f'<a class="tag cv" href="{WEB_URL}/cover?{_e(query)}" target="_blank" '
            f'title="Сопроводительное письмо под эту вакансию (стиль — на странице)">✉️</a>')
    out += (f'<a class="tag cv" href="{WEB_URL}/prep?url={quote(link, safe="")}&amp;date={day.isoformat()}" '
            f'target="_blank" title="Подготовка к интервью: что сказать, истории, вопросы">🎤</a>')
    applied = f"company={quote(job.company or '', safe='')}&title={quote(job.title or '', safe='')}&url={quote(link, safe='')}"
    out += (f'<a class="tag cv" href="{WEB_URL}/applications?{_e(applied)}" target="_blank" '
            f'title="Записать отклик в applications_manual.toml (форма на странице «Отклики»)">✅ откликнулся</a>')
    return out


def _outreach_html(job) -> str:
    """Строка под вакансией топа: поиск людей в LinkedIn и кнопка «скопировать черновик».

    В LinkedIn скрипт не ходит — ссылки открывает и сообщение отправляет пользователь.
    """
    links = [(label, url) for label, url in outreach.search_links(job.company)]
    if outreach.telegram_contact(job):
        links.insert(0, ("автор в Telegram", outreach.telegram_contact(job)))
    parts = [f'<a href="{_e(url)}" target="_blank" rel="noopener">🔎 {_e(label)}</a>' for label, url in links]
    msg = outreach.draft(job)
    parts.append(f'<button class="tag copy" data-msg="{_e(msg)}" title="{_e(msg)}">✉️ рекрутеру</button>')
    ref = outreach.referral_draft(job)
    parts.append(f'<button class="tag copy" data-msg="{_e(ref)}" title="Просьба о реферале инженеру компании: '
                 f'{_e(ref)}">🤝 реферал</button>')
    return f'<div class="reach">{" · ".join(parts)}</div>'


def _contacts_html(job) -> str:
    """Прямые контакты из текста вакансии (почта, Telegram, LinkedIn) — у любой вакансии, где они есть."""
    text = getattr(job, "description", "") or getattr(job, "text", "")
    found = outreach.contacts(text, getattr(job, "channel", ""))
    if not found:
        return ""
    icons = {"email": "✉", "telegram": "✈", "linkedin": "in"}
    links = [f'<a href="{_e(href)}" target="_blank" rel="noopener">{icons[kind]} {_e(label)}</a>'
             for kind, label, href in found[:4]]
    return f'<div class="reach" title="контакты из текста вакансии">📇 {" · ".join(links)}</div>'


def _levels(job) -> str:
    """Вторая строка в колонке зарплаты: медиана по стране с levels.fyi, со ссылкой на источник."""
    note = getattr(job, "levels_note", "")
    if not note:
        return ""
    return (f'<div class="small"><a href="{_e(job.levels_url)}" target="_blank" rel="noopener" '
            f'title="Software Engineer, все грейды, total compensation (оклад + бонусы + акции)">📊 {_e(note)}</a></div>')


def _missing(job) -> str:
    """Чего не хватает: ключевые навыки, потом nice-to-have."""
    core = ", ".join(getattr(job, "cv_core_missing", []))
    nice = ", ".join(getattr(job, "cv_nice_missing", []))
    return "; ".join(filter(None, [core, f"nice-to-have: {nice}" if nice else ""])) or "—"


def _fit_cell(job) -> str:
    """Fit % полоской: зелёная от 85, светло-зелёная от 70, жёлтая от 50, красная ниже."""
    fit = getattr(job, "cv_fit", -1)
    if fit < 0:
        return '<td data-sort="-1" class="small">—</td>'
    color = ("rgba(34,197,94,.55)" if fit >= 85 else "rgba(134,239,172,.6)" if fit >= 70
             else "rgba(250,204,21,.55)" if fit >= 50 else "rgba(248,113,113,.5)")
    tip = cv_match.skills_line(job)
    return (f'<td data-sort="{fit}" title="{_e(tip)}"><div class="fit"><i style="width:{fit}%;background:{color}"></i>'
            f"<b>{fit}%</b></div></td>")


def _table(headers: list[str], rows: list[str], table_id: str = "") -> str:
    head = "".join(f"<th>{_e(h)}</th>" for h in headers)
    attr = f' id="{table_id}"' if table_id else ""
    wrap = "wrap main-wrap" if table_id == "main" else "wrap"
    return f'<div class="{wrap}"><table{attr}><thead><tr>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'


def _applications_html(stats: dict) -> str:
    """Блок «📬 Отклики» над таблицей: счётчики и кому напомнить о себе (вся история — на странице «Отклики»)."""
    digest = stats.get("applications")
    if not digest:
        return ""
    c = digest["counts"]
    parts = [f'<div class="warning">⚠️ {_e(digest["warning"])}</div>'] if digest.get("warning") else []
    parts += [
        '<div class="sub"><b>📬 Отклики:</b> '
        f"🟢 этапы {c.get('active', 0) + c.get('offer', 0)} · 🟡 ждём ответа {c.get('waiting', 0)} · "
        f"🟠 отказался сам {c.get('declined', 0)} · 🔴 отказ {c.get('rejected', 0)} — "
        '<a href="applications.html">все отклики</a></div>'
    ]
    if digest["nudge"]:
        items = "".join(f"<li><b>{_e(company)}</b> — {days} дн. без ответа после этапа; последнее: {_e(subject)}</li>"
                        for company, days, subject in digest["nudge"])
        parts.append(f'<div class="small"><b>Напомнить о себе:</b><ul>{items}</ul></div>')
    return "".join(parts)


def render(day: date, jobs: list, stats: dict, tg_jobs: list | None = None) -> str:
    """HTML-страница отчёта. Состав разделов тот же, что в Markdown-отчёте."""
    tg = [j for j in (tg_jobs or []) if not j.lang_required and not j.staffing]
    main = [j for j in jobs if not j.lang_required and not j.staffing]
    staffing = [j for j in [*jobs, *(tg_jobs or [])] if j.staffing and not j.lang_required]
    refused = [j for j in [*main, *tg] if report._was_rejected(j)]
    main = [j for j in main if not report._was_rejected(j)]
    tg = [j for j in tg if not report._was_rejected(j)]
    top_ids = {id(j) for j, _ in report.pick_review(main, tg)}

    ordered = sorted([*main, *tg], key=tiers.sort_key)
    rows = []
    for j in ordered:
        status = report._status(j)
        is_new = j.is_new_today and not report._blocked(j)
        is_top = id(j) in top_ids
        marks = "; ".join(j.marks) if hasattr(j, "channel") else report._marks(j)
        marks = "; ".join(m for m in marks.split("; ") if m and m != "🆕" and not m.startswith(("💰", "🧩")))
        fit = cv_match.summary(j)
        fit_class = "ok" if j.cv_full else ""
        low_pay = salary.below_min(j)
        pay_attr = ' class="warn" title="вилка из вакансии ниже порога [salary] min_monthly_eur"' if low_pay else ""
        tags = ('<span class="tag new">новое</span>' if is_new else "") + ('<span class="tag top">топ</span>' if is_top else "")
        score = getattr(j, "score", 0)  # приоритет компании из companies.txt
        roots = f' <span class="tag" title="приоритет компании (companies.txt)">{"★" * score}</span>' if score else ""
        status_class = "ok" if status.startswith("✅") else "warn"
        contact = f' <span class="small">{_e(j.contact)}</span>' if getattr(j, "contact", "") and not j.url else ""
        rows.append(
            f'<tr data-new="{int(is_new)}" data-top="{int(is_top)}" data-ok="{int(status.startswith("✅"))}" '
            f'data-check="{int(status.startswith("❓"))}" data-reloc="{int(status.startswith("🌍"))}" '
            f'data-fit="{int(bool(j.cv_full))}" data-prio="{int(score > 0)}" data-fit80="{int(j.cv_fit >= 80)}" '
            f'data-pay="{int(salary.meets_min(j))}">'
            f'<td data-sort="{j.tier}">{_e(tiers.NAMES[j.tier])}</td>'
            f'<td class="n">{j.relevance}</td>'
            f"{_fit_cell(j)}"
            f'<td data-sort="{"jmsl".find(j.grade[:1]) if j.grade else 9}">{_e(salary.GRADE_NAMES.get(j.grade, "—"))}</td>'
            f"<td>{_e(j.company)}{roots}</td>"
            f'<td>{tags}<a href="{_e(_link(j))}" target="_blank" rel="noopener">{_e(j.title)}</a>{contact}'
            f'{_cv_link(j, day)}{_contacts_html(j)}{_outreach_html(j) if is_top else ""}</td>'
            f"<td>{_e(j.location or 'Remote')}</td>"
            f'<td class="{status_class}">{_e(status)}</td>'
            f"<td{pay_attr}>"
            f"{_e(j.salary_note)}{_levels(j)}</td>"
            f"<td>{_e(_date(j))}</td>"
            f'<td class="small">{_e(report._source(j))}</td>'
            f'<td class="small {fit_class}">{_e(fit)}</td>'
            f'<td class="small">{_e(_missing(j))}</td>'
            f'<td class="small">{_linkify(_e(marks))}</td></tr>'
        )
    new_count = sum('data-new="1"' in r for r in rows)
    ok_count = sum('data-ok="1"' in r for r in rows)
    check_count = sum('data-check="1"' in r for r in rows)
    reloc_count = sum('data-reloc="1"' in r for r in rows)
    fit_count = sum('data-fit="1"' in r for r in rows)
    prio_count = sum('data-prio="1"' in r for r in rows)
    fit80_count = sum('data-fit80="1"' in r for r in rows)
    pay_count = sum('data-pay="1"' in r for r in rows)
    pay_min = salary.min_monthly()

    refused_rows = [
        f"<tr><td>{_e(j.company)}</td>"
        f'<td><a href="{_e(_link(j))}" target="_blank" rel="noopener">{_e(j.title)}</a></td>'
        f"<td>{_e(j.location)}</td><td>{_e(_date(j))}</td>"
        f'<td class="small">{_e("; ".join(filter(None, [j.applied_status, *j.history_marks])))}</td></tr>'
        for j in sorted(refused, key=lambda j: j.company.lower())
    ]
    rejected = [j for j in stats.get("rejected", []) if not report._was_rejected(j)]
    rejected_rows = [
        f'<tr><td class="n">{j.relevance}</td><td>{_e(j.company)}</td>'
        f'<td><a href="{_e(_link(j))}" target="_blank" rel="noopener">{_e(j.title)}</a></td>'
        f"<td>{_e(j.reject)}</td><td class=\"small\">{_e(j.reject_quote)}</td>"
        f'<td class="small">{_e(report._source(j))}</td></tr>'
        for j in report.pick_rejected(rejected)
    ]
    staffing_rows = [
        f"<tr><td>{_e(j.company)}</td>"
        f'<td><a href="{_e(_link(j))}" target="_blank" rel="noopener">{_e(j.title)}</a></td>'
        f"<td>{_e(j.location)}</td><td class=\"small\">{_e(j.staffing)}</td></tr>"
        for j in sorted(staffing, key=lambda j: j.company.lower())
    ]

    parts = [
        "<!doctype html>", '<html lang="ru"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>Вакансии {day.isoformat()}</title><style>{_CSS}</style></head><body>",
        f"<h1>Вакансии за {day.isoformat()}</h1>",
        f'<div class="sub">Всего {len(rows)} · новых {new_count} · в топе {len(top_ids)} · '
        f"✅ {ok_count} · 🌍 {reloc_count} · ❓ {check_count} · ранее отказ {len(refused)} · отброшено фильтрами {len(rejected)}</div>",
        _applications_html(stats),
        '<div class="bar">',
        f'<button data-mode="all" class="on">Все ({len(rows)})</button>',
        f'<button data-mode="new">🆕 Новое ({new_count})</button>',
        f'<button data-mode="top">⭐ Топ ({len(top_ids)})</button>',
        (f'<button data-mode="prio" title="компании с приоритетом 1–3 в companies.txt">★ Приоритетные ({prio_count})</button>'
         if prio_count else ""),
        f'<button data-mode="ok">✅ Подтверждено ({ok_count})</button>',
        f'<button data-mode="fit80">Fit ≥ 80% ({fit80_count})</button>',
        f'<button data-mode="fit">✔ Полное соответствие ({fit_count})</button>',
        (f'<button data-mode="pay" title="Только вакансии, где в тексте указана вилка с верхней границей от {pay_min} '
         f'EUR/мес; оценка по базе и levels.fyi не считаются">💶 Вилка ≥ {pay_min / 1000:g}k ({pay_count})</button>' if pay_min else ""),
        f'<button data-mode="reloc">🌍 Релокация ({reloc_count})</button>',
        f'<button data-mode="check">❓ Проверить ({check_count})</button>',
        '<input id="q" type="search" placeholder="Поиск: компания, роль, навык, локация…">',
        '<span class="count" id="count"></span></div>',
        _table(["Категория", "Балл", "Fit %", "Грейд", "Компания", "Вакансия", "Локация", "Статус", "Зарплата, EUR/мес", "Дата", "Источник",
                "Соответствие резюме", "Не хватает", "Пометки"],
               rows, "main"),
    ]
    if refused_rows:
        parts += [f"<details><summary><h2>Ранее отказ — {len(refused_rows)}</h2></summary>",
                  _table(["Компания", "Вакансия", "Локация", "Дата", "Отклики"], refused_rows), "</details>"]
    if rejected_rows:
        parts += [f"<details><summary><h2>Отброшено фильтрами — {len(rejected)} (показано {len(rejected_rows)})</h2></summary>",
                  '<div class="small">Полный список — state/rejected_latest.tsv</div>',
                  _table(["Исх. балл", "Компания", "Вакансия", "Причина", "Цитата", "Источник"], rejected_rows), "</details>"]
    gaps = report.skill_gaps(main, tg)
    if gaps["total"]:
        nice, total = dict(gaps["nice"]), gaps["total"]
        gap_rows = []
        for name, n in gaps["core"]:
            extra = nice.pop(name, 0)
            extra_cell = f"{extra} ({round(100 * extra / total)}%)" if extra else "—"
            gap_rows.append(f'<tr><td>{_e(name)}</td><td class="n">{n} ({round(100 * n / total)}%)</td>'
                            f'<td class="n">{extra_cell}</td></tr>')
        parts += [f"<details><summary><h2>📊 Каких навыков чаще всего не хватает — по {gaps['total']} подходящим "
                  "вакансиям (Backend, ML / AI)</h2></summary>",
                  _table(["Навык", "Ключевой (нет в резюме)", "Nice-to-have"], gap_rows), "</details>"]
    if staffing_rows:
        parts += [f"<details><summary><h2>Staffing / outsourcing — {len(staffing_rows)}</h2></summary>",
                  _table(["Компания", "Вакансия", "Локация", "Причина"], staffing_rows), "</details>"]
    parts += [f"<script>{_JS}</script></body></html>"]
    return "\n".join(parts)
