"""HTML-страница со статусами откликов по переписке в почте (в стиле отчёта по вакансиям)."""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from urllib.parse import quote

from . import geo, mail
from .html_report import _CSS, _JS, _e, _table

# Итоговые статусы и цвета строк
OFFER, ACTIVE, WAITING, DECLINED, REJECTED = "offer", "active", "waiting", "declined", "rejected"
STATUS = {  # статус -> (метка, CSS-класс строки)
    OFFER: ("🎉 Оффер", "c-green"),
    ACTIVE: ("🟢 Идут этапы", "c-green"),
    WAITING: ("🟡 Ждём ответа", "c-yellow"),
    DECLINED: ("🟠 Отказался сам", "c-orange"),
    REJECTED: ("🔴 Отказ", "c-red"),
}
# Без ответа дольше этого срока — отказ: две недели молчания после отклика или этапа — это уже не ожидание
TIMEOUT_DAYS = int(geo.S.get("mail", {}).get("timeout_days", 14))

# статусы из applications_manual.toml (по компании может быть несколько строк: «Отклик, Отказ»)
_SHEET_STAGE_RX = re.compile(r"собес|интервью|interview|тестов|оффер|этап", re.I)
_SHEET_DECLINED_RX = re.compile(r"отказался|отказалась", re.I)
_SHEET_REJECTED_RX = re.compile(r"^отказ\b", re.I)
_SHEET_IGNORED_RX = re.compile(r"игнор", re.I)
KIND_NAMES = {
    mail.OFFER: "оффер", mail.INTERVIEW: "этап", mail.APPLIED: "отклик", mail.REJECTED: "отказ", mail.OTHER: "письмо",
}
_CSS_COLORS = """
tr.c-green { background: rgba(34,197,94,.13); } tr.c-yellow { background: rgba(234,179,8,.15); }
tr.c-orange { background: rgba(249,115,22,.17); } tr.c-red { background: rgba(239,68,68,.11); }
tbody tr.c-green:hover, tbody tr.c-yellow:hover, tbody tr.c-orange:hover, tbody tr.c-red:hover { filter: brightness(.97); }
.legend span { display: inline-block; padding: 1px 8px; border-radius: 6px; margin-right: 6px; }
"""


def _thread_link(address: str, thread: str) -> str:
    return f"https://mail.google.com/mail/?authuser={quote(address)}#all/{thread}"


def _last_cell(event, address: str) -> str:
    """Последнее событие: ссылка на письмо или (для Telegram / LinkedIn) текст и источник."""
    if event.thread:
        return f'<a href="{_e(_thread_link(address, event.thread))}">{_e(event.subject[:90])}</a>'
    return f"{_e(event.subject[:120])} <i>({_e(event.sender)})</i>"


def effective_status(app, in_applied: str, now: datetime) -> tuple[str, str]:
    """Итоговый статус компании -> (статус, пояснение).

    🟢 этапы: приглашение на этап по почте или собеседование / тестовое в applications_manual.toml;
    🟡 ждём ответа: был отклик, ответа ещё нет, но прошло не больше TIMEOUT_DAYS;
    🟠 отказался сам: так отмечено в applications_manual.toml;
    🔴 отказ: письмо с отказом, «Отказ» в applications_manual.toml или молчание дольше TIMEOUT_DAYS.
    Отказ как самое последнее событие важнее этапа из файла. Новый отклик в почте после письма-отказа — повторная попытка: она снова ждёт ответа.
    """
    sheet = [s.strip() for s in in_applied.split(",") if s.strip()] if in_applied else []
    silent = (now - app.last.when).days
    if app.status == mail.OFFER:
        return OFFER, ""
    stage_rows = [s for s in sheet if _SHEET_STAGE_RX.search(s)]
    # статусы по компании без дат, поэтому отказ как самое последнее событие важнее этапа
    if stage_rows and app.last.kind != mail.REJECTED:
        return ACTIVE, f"по откликам: {stage_rows[0]}"
    rejects = [e.when for e in app.events if e.kind == mail.REJECTED]
    reapplied = bool(rejects) and any(e.kind == mail.APPLIED and e.when > max(rejects) for e in app.events)
    base, why = app.status, ""
    if reapplied:
        base, why = mail.APPLIED, "повторный отклик после отказа"
    elif any(_SHEET_DECLINED_RX.search(s) for s in sheet):
        return DECLINED, ""
    elif any(_SHEET_REJECTED_RX.search(s) for s in sheet):
        return REJECTED, "отметка в applications_manual.toml"
    if base == mail.REJECTED:
        return REJECTED, "отказ в переписке"
    if silent > TIMEOUT_DAYS:
        after = "этапа" if base == mail.INTERVIEW else "отклика"
        return REJECTED, f"нет ответа {silent} дн. после {after}"
    if base == mail.INTERVIEW:
        return ACTIVE, why
    if not why and any(_SHEET_IGNORED_RX.search(s) for s in sheet):
        why = "отмечено «Игнор»"
    return WAITING, why or f"{silent} дн. без ответа"


class StatusIndex:
    """Статусы откликов по компаниям — для пометок в отчёте по вакансиям и блока «📬 Отклики».

    Строится из локального кэша писем и applications_manual.toml, без запросов в Gmail
    (кэш обновляет applications.py).
    """

    NUDGE_AFTER_DAYS = 5  # после этапа тишина дольше — стоит напомнить о себе
    RECENT_DAYS = 2  # «что изменилось» — события за последние двое суток

    def __init__(self, sheet=None):
        import json

        from . import config
        from .keys import company_key

        self._key = company_key
        messages = []
        if config.MAIL_CACHE_FILE.exists():
            messages = list(json.loads(config.MAIL_CACHE_FILE.read_text(encoding="utf-8")).values())
        apps, _ = mail.summarize(messages)
        self.now = datetime.now(timezone.utc)
        self.items = []  # (ключ компании, app, статус, пояснение)
        for app in apps:
            in_applied = sheet.company_statuses(app.company) if sheet else ""
            status, why = effective_status(app, in_applied, self.now)
            self.items.append((company_key(app.company), app, status, why))

    def lookup(self, company: str) -> str:
        """«🔴 Отказ — отказ в переписке, 03.09» или '' — откликов в эту компанию не было."""
        key = self._key(company)
        if len(key) < 3:
            return ""
        for k, app, status, why in self.items:
            if k == key or (min(len(k), len(key)) >= 4 and (k.startswith(key) or key.startswith(k))):
                return f"{STATUS[status][0]}{f' — {why}' if why else ''}, {app.last.when:%d.%m}"
        return ""

    def digest(self) -> dict:
        """Что изменилось за двое суток и кому пора напомнить о себе."""
        recent, nudge = [], []
        for _, app, status, _why in self.items:
            for e in app.ordered:
                if e.kind != mail.OTHER and (self.now - e.when).days < self.RECENT_DAYS:
                    recent.append((e.when, app.company, KIND_NAMES[e.kind], e.subject[:90]))
            silent = (self.now - app.last.when).days
            if status == ACTIVE and silent >= self.NUDGE_AFTER_DAYS:
                nudge.append((app.company, silent, app.last.subject[:90]))
        counts = {s: sum(1 for *_, st, _w in self.items if st == s) for s in STATUS}
        return {
            "recent": sorted(recent, reverse=True), "nudge": sorted(nudge, key=lambda x: -x[1]), "counts": counts,
            "warning": mail.stale_warning(),
        }


def render(day: date, apps: list, unknown: list, address: str, sheet=None, warning: str = "") -> str:
    now = datetime.now(timezone.utc)
    order = list(STATUS)
    items = []
    for app in apps:
        in_applied = sheet.company_statuses(app.company) if sheet else ""
        status, why = effective_status(app, in_applied, now)
        items.append((app, in_applied, status, why))
    # сверху живое: этапы, ожидание; ниже — отказы; внутри — свежие первыми
    items.sort(key=lambda x: (order.index(x[2]), -x[0].last.when.timestamp()))
    rows, counts = [], {}
    for app, in_applied, status, why in items:
        label, css = STATUS[status]
        counts[status] = counts.get(status, 0) + 1
        silent = (now - app.last.when).days
        history = " → ".join(f"{KIND_NAMES[e.kind]} {e.when:%d.%m}" for e in app.ordered if e.kind != mail.OTHER)
        rows.append(
            f'<tr class="{css}" data-{status}="1">'
            f"<td><b>{_e(app.company)}</b></td>"
            f'<td data-sort="{order.index(status)}"><b>{_e(label)}</b>'
            f'{f"<br><span class=small>{_e(why)}</span>" if why else ""}</td>'
            f'<td class="n">{app.stages or ""}</td>'
            f'<td data-sort="{app.first:%Y%m%d}">{app.first:%Y-%m-%d}</td>'
            f'<td data-sort="{app.last.when:%Y%m%d}">{app.last.when:%Y-%m-%d}</td>'
            f'<td class="n">{silent}</td>'
            f'<td class="small">{_e(history)}</td>'
            f'<td class="small">{_last_cell(app.last, address)}</td>'
            f'<td class="n">{len(app.events)}</td>'
            "</tr>"
        )
    unknown_rows = [
        f'<tr><td>{datetime.fromtimestamp(m["date"], tz=timezone.utc):%Y-%m-%d}</td><td>{_e(KIND_NAMES[m["kind"]])}</td>'
        f'<td class="small">{_e(m["from"])}</td>'
        f'<td><a href="{_e(_thread_link(address, m["thread"]))}">{_e(m["subject"][:110])}</a></td></tr>'
        for m in sorted(unknown, key=lambda m: -m["date"])
    ]
    buttons = [("all", f"Все ({len(rows)})")]
    buttons += [(s, f"{STATUS[s][0]} ({counts[s]})") for s in order if counts.get(s)]
    legend = (
        '<div class="sub legend">'
        '<span class="c-green" style="background:rgba(34,197,94,.13)">зелёный — этапы, оффер</span>'
        f'<span style="background:rgba(234,179,8,.15)">жёлтый — ждём ответа, до {TIMEOUT_DAYS} дней</span>'
        '<span style="background:rgba(249,115,22,.17)">оранжевый — отказался сам</span>'
        f'<span style="background:rgba(239,68,68,.11)">красный — отказ: письмо, отметка или {TIMEOUT_DAYS}+ дней тишины</span>'
        "</div>"
    )
    parts = [
        "<!doctype html><html lang='ru'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width, initial-scale=1'>",
        f"<title>Отклики — {day.isoformat()}</title><style>{_CSS}{_CSS_COLORS}</style></head><body>",
        f"<h1>Отклики по переписке — {day.isoformat()}</h1>",
        f'<div class="warning">⚠️ {_e(warning)}</div>' if warning else "",
        f'<div class="sub">Почта {_e(address)} и applications_manual.toml (Telegram, LinkedIn) · компаний {len(rows)} · '
        f"статус — по переписке и ручным событиям · только чтение почты, "
        f"ничего не отправлялось</div>",
        legend,
        '<div class="bar">',
        *(f'<button data-mode="{m}"{" class=on" if m == "all" else ""}>{_e(t)}</button>' for m, t in buttons),
        '<input id="q" type="search" placeholder="Поиск по компании, теме письма…"><span id="count" class="count"></span></div>',
        _table(["Компания", "Статус", "Этапов", "Первое событие", "Последнее событие", "Дней тишины", "История",
                "Последнее событие", "Событий"], rows, "main"),
    ]
    if unknown_rows:
        parts += [
            f"<details><summary><h2>Компания не определена — {len(unknown_rows)}</h2></summary>",
            '<p class="small">Письма похожи на отклик / этап / отказ, но компанию не видно ни в отправителе, ни в теме. '
            "Название можно задать в settings.toml → [mail.company_aliases] или посмотреть письмо по ссылке.</p>",
            _table(["Дата", "Тип", "Отправитель", "Тема"], unknown_rows), "</details>",
        ]
    parts += [f"<script>{_JS}</script></body></html>"]
    return "\n".join(parts)
