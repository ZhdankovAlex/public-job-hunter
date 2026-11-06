"""Публичные Telegram-каналы через веб-превью t.me/s/{канал} (без Telethon и API)."""
import html
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from . import config as c
from . import filters, geo, language, progress
from .verify import ABROAD_OK_RX, HIDE_RUSSIA_ONLY, ru_only
from .http import FetchError
from .models import parse_date
from .keys import company_key

log = logging.getLogger(__name__)

# сайты-агрегаторы, на которые ведут ссылки в постах: живость такой ссылки проверяется отдельно
AGGREGATOR_HOSTS = tuple(c._TELEGRAM.get("aggregator_hosts", []))
B_OPEN, B_CLOSE = "\x01", "\x02"  # границы <b> в тексте поста

_MESSAGE_SPLIT = '<div class="tgme_widget_message_wrap'
_TEXT_RX = re.compile(r'<div class="tgme_widget_message_text js-message_text[^>]*>(.*?)</div>', re.S)
_EMOJI_RX = re.compile(r"<i class=\"emoji\"[^>]*>(?:<b>)?([^<]*)(?:</b>)?</i>")
_LINK_RX = re.compile(r'<a\s[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S)
_HANDLE_RX = re.compile(r"(?<![\w/])@[A-Za-z][A-Za-z0-9_]{3,}")
_HASHTAG_RX = re.compile(r"#(\w+)")
_SALARY_RX = re.compile(
    r"(?:от|до|from|up to)?\s*[$€£]?\s*\d[\d\s.,]*(?:k|к)?\s*(?:[-–—]\s*[$€£]?\s*\d[\d\s.,]*(?:k|к)?)?\s*"
    r"(?:[$€£₽]|usd|eur|rsd|руб)",
    re.I,
)
_FORMAT_ONLY_RX = re.compile(
    r"remote|удал[её]н|hybrid|гибрид|офис|office|relocat|релокац|" + c.HOME_RX.pattern, re.I
)
_COMPANY_NAME_RX = re.compile(r"[A-Z][A-Za-z0-9 .&-]+")
_SALARY_WORD_RX = re.compile(r"зарплат|\bзп\b|вилк|salary|оклад|оплата|compensation|\bnet\b|gross", re.I)
_FIELD_EMOJI = "🏢📍💻📅🖥💰✈👉"
_REMOTE_CONTEXT_RX = re.compile(r"удал[её]н|remote|из любой точки мира|worldwide|anywhere", re.I)
_WORLDWIDE_CONTEXT_RX = re.compile(r"из любой точки мира|worldwide|anywhere|без привязки к стране", re.I)


_FORMAT_LABELS = [
    ("remote", re.compile(r"remote|удал[её]н", re.I)),
    ("hybrid", re.compile(r"hybrid|гибрид", re.I)),
    ("onsite", re.compile(r"on-?site|office|офис", re.I)),
]
_EXTRA_LABELS = [
    ("relocation support", re.compile(r"relocat|релокац", re.I)),
    ("visa", re.compile(r"\bvisa|\bвиз", re.I)),
]
_TAG_LABELS = {
    "remote": "remote", "hybrid": "hybrid", "office": "onsite", "onsite": "onsite",
    "relocation": "relocation support",
}


def _labels(table, *texts):
    joined = " ".join(texts)
    return [name for name, rx in table if rx.search(joined)]


def _short_place(text, limit=45):
    """Страна/город без свободного текста: обрезаем по скобке/тире, длинное выбрасываем."""
    text = re.split(r"[(—–·|]|\s-\s", text)[0].strip(" ,.)")
    if " " not in text and _labels(_FORMAT_LABELS, text):
        return ""  # "удаленно" / "Remote" — это формат, а не место
    return text if len(text) <= limit else ""


def _join_location(parts):
    seen, out = set(), []
    for part in filter(None, parts):
        if part.lower() not in seen and not any(part.lower() in s for s in seen):
            seen.add(part.lower())
            out.append(part)
    return ", ".join(out)


@dataclass
class TgJob:
    channel: str
    posted: datetime | None
    title: str
    company: str
    location: str  # локация / формат одной строкой
    salary: str
    url: str  # внешняя ссылка на вакансию, если есть
    contact: str  # @handle, если ссылки нет
    post_url: str
    text: str = ""  # где искать ключевые слова
    label: str = ""
    marks: list = field(default_factory=list)
    # заполняется при сверке с таблицами (те же поля, что у Job)
    applied_status: str = ""
    staffing: str = ""
    lang_required: bool = False
    is_new_today: bool = False
    relevance: int = 0
    reject: str = ""
    reject_quote: str = ""
    manual_check: str = ""
    penalties: list = field(default_factory=list)
    full_text: bool = True  # в посте полный текст вакансии (а не название или пересказ)
    cv_have: list = field(default_factory=list)
    cv_missing: list = field(default_factory=list)
    cv_mismatch: list = field(default_factory=list)
    cv_partial: list = field(default_factory=list)
    cv_full: bool = False
    cv_pick: str = ""
    cv_fit: int = -1  # Fit % вакансии и резюме (cv_match.fit_percent); -1 — не посчитан
    grade: str = ""  # junior / middle / senior / lead — из названия или требуемого опыта (salary.grade)
    cv_file: str = ""  # PDF резюме под вакансию, собранный автоматически (cvgen.auto_build), путь от корня проекта
    cv_core_have: list = field(default_factory=list)
    cv_core_missing: list = field(default_factory=list)
    cv_nice_have: list = field(default_factory=list)
    cv_nice_missing: list = field(default_factory=list)
    tier: int = 4
    mismatch_lang: str = ""
    salary_note: str = ""  # вилка: указанная или оценка по базе (salary.py)
    levels_note: str = ""  # медиана по стране вакансии с levels.fyi (levels.py)
    levels_url: str = ""
    matched_skills: list = field(default_factory=list)
    history_marks: list = field(default_factory=list)
    app_status: str = ""


def _to_text(raw):
    """HTML поста -> текст; <b> помечен B_OPEN/B_CLOSE, ссылки стоят как ⟦url⟧."""
    raw = re.sub(r"</?tg-emoji[^>]*>", "", raw)
    raw = _EMOJI_RX.sub(lambda m: m.group(1), raw)
    raw = _LINK_RX.sub(lambda m: f"{m.group(2)} ⟦{m.group(1)}⟧", raw)
    raw = re.sub(r"<br\s*/?>", "\n", raw)
    raw = re.sub(r"<b>", B_OPEN, raw)
    raw = re.sub(r"</b>", B_CLOSE, raw)
    raw = re.sub(r"<[^>]+>", "", raw)
    return html.unescape(raw).replace("\xa0", " ")


def _plain(text):
    text = re.sub(r"⟦[^⟧]*⟧", "", text)
    return text.replace(B_OPEN, "").replace(B_CLOSE, "").strip()


def _external_links(text):
    return [u for u in re.findall(r"⟦([^⟧]*)⟧", text) if u.startswith("http") and "//t.me/" not in u]


def fetch_posts(http, channel, cutoff):
    """Посты канала не старше cutoff: [(post_id, datetime, html, forwarded)]."""
    posts, before = [], None
    for _ in range(c.TELEGRAM_MAX_PAGES):
        r = http.get(f"https://t.me/s/{channel}", params={"before": before} if before else None)
        if r.status_code != 200:
            raise FetchError(f"t.me/s/{channel}: HTTP {r.status_code}")
        page = []
        for chunk in r.text.split(_MESSAGE_SPLIT)[1:]:
            post = re.search(r'data-post="[^"/]+/(\d+)"', chunk)
            when = re.findall(r'<time datetime="([^"]+)" class="time"', chunk)
            texts = _TEXT_RX.findall(chunk)
            if post and when and texts:
                page.append((
                    int(post.group(1)), parse_date(when[-1]), texts[-1],
                    "tgme_widget_message_forwarded_from" in chunk,
                ))
        if not page:
            if not posts:
                raise FetchError(f"t.me/s/{channel}: постов не найдено (канал закрыт или изменилась вёрстка)")
            break
        posts += [p for p in page if p[1] and p[1] >= cutoff]
        older = re.search(r'data-before="(\d+)"', r.text)
        if not older or min(p[1] for p in page if p[1]) < cutoff:
            break
        before = older.group(1)
    return posts


def _vacancy_link(links):
    """Ссылка на саму вакансию, если она есть среди ссылок поста; иначе первая."""
    for link in links:
        if re.search(r"vacanc|job|career|apply|position|hirify", link, re.I):
            return link
    return links[0] if links else ""


def parse_youritjob(channel, post_id, when, raw):
    """Один пост = одна вакансия; первая bold-строка — 'Должность, Компания/формат'."""
    text = _to_text(raw)
    head = re.match(rf"\s*{B_OPEN}(.*?){B_CLOSE}", text, re.S)
    if not head:
        return []
    lines = [l.strip() for l in _plain(head.group(1)).split("\n") if l.strip()]
    if not lines or "дайджест вакансий" in lines[0].lower():
        return []
    title, rest = lines[0].rstrip(","), ", ".join(lines[1:])
    if not rest and "," in title:
        title, rest = (p.strip() for p in title.split(",", 1))
    company, _, fmt = rest.rpartition(",") if "," in rest else (rest, "", "")
    if not fmt and _FORMAT_ONLY_RX.search(company):  # "QA Engineer, Remote" — компании нет
        company, fmt = "", company
    plain = _plain(text)
    if re.search(r"уборк", plain, re.I):  # не IT: клининг и т.п.
        return []
    tags = [t.lower() for t in _HASHTAG_RX.findall(plain)]
    if not tags and not c.TG_KEYWORD_RX.search(plain):
        return []
    # только короткие метки: место из заголовка, формат, релокация/виза
    location = _join_location([
        _short_place(fmt, 30),
        *_labels(_FORMAT_LABELS + _EXTRA_LABELS, fmt),
        *(_TAG_LABELS[t] for t in tags if t in _TAG_LABELS),
        *(t.title() for t in tags if c.HOME_RX.fullmatch(t)),  # хэштег домашней страны / города
    ])
    first_lines = [l.strip() for l in plain.split("\n") if l.strip()][:6]
    if not c.TG_ROLE_RX.search(title):
        # первая жирная строка бывает баннером ("This stack needs a Senior") — ищем должность ниже
        for line in first_lines[1:]:
            if len(line) <= 90 and c.TG_ROLE_RX.search(line):
                title, _, employer = line.partition(" / В ")
                company = company or employer
                break
    if not location:
        # формат отдельной строкой под заголовком: "Lisbon | Office-based", "Remote"
        for line in first_lines[1:]:
            if len(line) <= 40 and _FORMAT_ONLY_RX.search(line):
                location = _join_location([_short_place(line, 30), *_labels(_FORMAT_LABELS + _EXTRA_LABELS, line)])
                break
    # вилка: сначала строка с явным словом про оплату где угодно в посте, затем сумма в шапке
    from . import salary as salary_rules

    all_lines = plain.split("\n")
    salary = next(
        (l.strip()[:80] for l in all_lines if _SALARY_WORD_RX.search(l) and salary_rules.parse(l)), ""
    ) or next((m.group().strip() for l in all_lines[1:8] if (m := _SALARY_RX.search(l))), "")
    links = _external_links(text)
    handles = [h for h in _HANDLE_RX.findall(plain) if h.lower() != "@" + channel.lower()]
    return [TgJob(
        channel=channel, posted=when, title=title, company=company.strip() or "—",
        location=location, salary=salary, url=_vacancy_link(links),
        contact=handles[0] if handles else "", post_url=f"https://t.me/{channel}/{post_id}",
        text=plain,
    )]


def parse_af_job_board(channel, post_id, when, raw):
    """Один пост = подборка; блоки заканчиваются строкой '👉 Apply'."""
    lines = [l.strip() for l in _to_text(raw).split("\n")]
    jobs, block, intro = [], [], None
    for line in lines:
        block.append(line)
        if "👉" not in line or not _external_links(line):
            continue
        fields = {}
        title_idx = None
        for i, l in enumerate(block):
            p = _plain(l)
            if p and p[0] in _FIELD_EMOJI:
                fields.setdefault(p[0], p[1:].lstrip("️").strip())
                if title_idx is None:
                    title_idx = next((k for k in range(i - 1, -1, -1) if _plain(block[k])), None)
        if title_idx is not None:
            if intro is None:
                intro = "\n".join(block[:title_idx])
            jobs.append((_plain(block[title_idx]), fields, _external_links(line)[0]))
        block = []
    if not jobs:
        return []  # история компании, вебинар и т.п.
    intro = intro or ""
    intro_plain = _plain(intro)
    # компания на весь пост ("AUTODOC сейчас активно нанимает") — первый bold во вступлении
    post_company = next(
        (b.strip() for b in re.findall(rf"{B_OPEN}(.*?){B_CLOSE}", intro)
         if _COMPANY_NAME_RX.fullmatch(b.strip())),
        "",
    )
    result = []
    for title, f, url in jobs:
        fmt = f.get("💻") or f.get("📅") or f.get("🖥") or ""
        place = f.get("📍", "")
        if not fmt and not place and _REMOTE_CONTEXT_RX.search(intro_plain):
            fmt = "remote"
            place = "Worldwide" if _WORLDWIDE_CONTEXT_RX.search(intro_plain) else ""
        location = _join_location([
            _short_place(place),
            *_labels(_FORMAT_LABELS, fmt),
            *_labels(_EXTRA_LABELS, f.get("✈", "")),
        ])
        company = f.get("🏢") or post_company or "—"
        result.append(TgJob(
            channel=channel, posted=when, title=title, company=company, location=location,
            salary=f.get("💰", "") if re.search(r"\d", f.get("💰", "")) else "", url=url, contact="", post_url=f"https://t.me/{channel}/{post_id}",
            text=f"{title} {company}",
        ))
    return result


def _ru_format(line):
    """'удалённо (можно за пределами РФ)', 'Москва (офис)' -> короткие метки локации."""
    labels = _labels(_FORMAT_LABELS + _EXTRA_LABELS, line)
    if ABROAD_OK_RX.search(line):
        labels.append("worldwide")
    elif labels == ["remote"] and ru_only(line):
        labels.append("Russia")
    place = re.sub(
        r"\(.*?\)|\b(?:удал[её]нн?\w*|гибрид\w*|офис\w*|или|можно|полн\w*|график|на выбор|формат|работа)\b"
        r"|из любой точки мира|\bв\b",
        " ", line, flags=re.I,
    )
    place = re.sub(r"\s+", " ", place).strip(" ,.;:/+-")
    return _join_location([_short_place(place, 30) if place else "", *labels])


def parse_habr_digest(channel, post_id, when, raw):
    """@habr_career: пост-подборка, строка = 'Должность в Компания ⟦u.habr.com/…⟧. Город. Формат.'"""
    text = _to_text(raw)
    tags = " ".join(f"#{t}" for t in _HASHTAG_RX.findall(_plain(text)))
    jobs = []
    for line in text.split("\n"):
        m = re.match(r"\s*(.+?) в (.+?) ⟦(https?://u\.habr\.com/[^⟧]+)⟧\.?\s*(.*)", line)
        if not m:
            continue
        title, company, link, rest = (_plain(x).strip() for x in m.groups())
        fmt = " ".join([rest, "удалённо" if "#remote" in tags else ""])
        jobs.append(TgJob(
            channel=channel, posted=when, title=title, company=company, location=_ru_format(fmt) or rest[:40],
            salary="", url=link, contact="", post_url=f"https://t.me/{channel}/{post_id}",
            text=f"{title} {company} {rest} {tags}", full_text=False,
        ))
    return jobs


_ABROAD_REMOTE_RX = re.compile(r"\(?\s*вне\s+(?:РФ|России)(?:\s+и\s+(?:РБ|Беларуси))?\s*\)?", re.I)


def parse_zarubezhom(channel, post_id, when, raw):
    """@zarubezhom_jobs: «**Должность в Компания**», «📍 место», «💎 требования», ссылка «Узнать подробнее … тут».

    Дайджесты («Дайджест вакансий…») пропускаются: в них только ссылки на отдельные посты.
    """
    text = _to_text(raw)
    head = re.match(rf"\s*{B_OPEN}(.*?){B_CLOSE}", text, re.S)
    if not head:
        return []
    first = " ".join(_plain(head.group(1)).split())
    if re.search(r"дайджест|подборк", first, re.I) or " в " not in first:
        return []
    title, _, company = first.rpartition(" в ")
    plain = _plain(text)
    place = next((l.lstrip("📍 ").strip() for l in plain.split("\n") if l.strip().startswith("📍")), "")
    # «Remote (вне РФ и РБ)» — удалёнка откуда угодно, кроме России и Беларуси
    place = _ABROAD_REMOTE_RX.sub(", worldwide", place).strip(" ,")
    place = re.sub(r"(?i)удал[её]нн?о|удал[её]нка", "Remote", place)
    links = _external_links(text.split("Узнать подробнее", 1)[-1]) or _external_links(text)
    return [TgJob(
        channel=channel, posted=when, title=title.strip(), company=company.strip() or "—", location=place,
        salary="", url=links[0] if links else "", contact="", post_url=f"https://t.me/{channel}/{post_id}",
        text=plain, full_text=False,
    )]


def parse_relocatesme(channel, post_id, when, raw):
    """@relocatesme: посты трёх видов, реклама без этих признаков пропускается.

    «**Должность в Компания**», «📍 место» — как в @zarubezhom_jobs;
    «**Должность**», строка формата, «Компания: X — описание», «ЗП:», «Откликнуться: ссылка»;
    «**Должность** ⟦ссылка⟧», «Senior · #hybrid · Warsaw» — компания не названа.
    """
    text = _to_text(raw)
    if "📍" in text:
        return parse_zarubezhom(channel, post_id, when, raw)
    head = re.match(rf"\s*{B_OPEN}(.*?){B_CLOSE}", text, re.S)
    if not head:
        return []
    plain = _plain(text)
    lines = [l.strip() for l in plain.split("\n") if l.strip()]
    company = next((l.split(":", 1)[1].split(" — ")[0].strip() for l in lines if l.startswith("Компания:")), "")
    short = next((l for l in lines if " · " in l and "#" in l), "")
    if not company and not short:
        return []
    if company.lower() == "компания мечты":  # заглушка канала, когда компания не раскрыта
        company = ""
    if short:
        place = ", ".join(p.strip(" #") for p in short.split(" · ")[1:])
    else:
        place = _ru_format(re.sub(r"[()]", " ", lines[1]))
    pay = next((l.split(":", 1)[1].strip() for l in lines if l.startswith("ЗП:")), "")
    links = [u for u in _external_links(text) if "updatecv" not in u]
    return [TgJob(
        channel=channel, posted=when, title=" ".join(_plain(head.group(1)).split()), company=company or "—",
        location=place, salary=pay if _SALARY_RX.search(pay) else "", url=_vacancy_link(links), contact="",
        post_url=f"https://t.me/{channel}/{post_id}", text=plain, full_text=False,
    )]


def parse_jtbl(channel, post_id, when, raw):
    """@jtbl_vacancy: «**Должность в Отрасль**», хештеги, «Требования:», «Локация: 📍…», «Контакт для отклика: @…».

    Компания в постах не называется — вместо неё отрасль из заголовка («Fintech», «Travel Tech»).
    """
    text = _to_text(raw)
    head = re.match(rf"\s*{B_OPEN}(.*?){B_CLOSE}", text, re.S)
    if not head:
        return []
    first = " ".join(_plain(head.group(1)).split())
    title, sep, domain = first.rpartition(" в ")
    if not sep:
        title, domain = first, ""
    plain = _plain(text)
    place = next((l.split(":", 1)[1].strip(" 📍") for l in plain.split("\n") if l.strip().lower().startswith("локация")), "")
    place = re.sub(r"(?i)удал[её]нн?о", "Remote", place).replace("из любой точки мира", "worldwide").strip(" .")
    handles = [h for h in _HANDLE_RX.findall(plain) if h.lower() != "@" + channel.lower()]
    return [TgJob(
        channel=channel, posted=when, title=title.strip(), company=f"{domain} (компания не названа)" if domain else "—",
        location=place, salary="", url="", contact=handles[0] if handles else "",
        post_url=f"https://t.me/{channel}/{post_id}", text=plain,
    )]


def parse_career_sports(channel, post_id, when, raw):
    """@career_sports (канал Sports.ru): '**Должность** ⟦ссылка⟧', ниже '📍 формат' и '🔵 уровень'."""
    jobs = parse_youritjob(channel, post_id, when, raw)
    for job in jobs:
        job.company = "Sports.ru"
        place = next((l.lstrip("📍 ").strip() for l in job.text.split("\n") if l.startswith("📍")), "")
        if place:
            job.location = _ru_format(place.replace("На выбор:", ""))
    return jobs


def parse_job_python(channel, post_id, when, raw):
    """@job_python: строка хештегов, затем жирные 'Компания' и 'Должность', 'Формат работы: …'."""
    text = _to_text(raw)
    plain = _plain(text)
    bolds = [_plain(b) for b in re.findall(rf"{B_OPEN}(.*?){B_CLOSE}", text, re.S)]
    bolds = [b for b in bolds if b and not b.startswith("☑")]
    if len(bolds) < 2:
        return []
    company, title = bolds[0], bolds[1]
    fmt = next((l.split(":", 1)[1].strip() for l in plain.split("\n") if l.lower().startswith("формат работы:")), "")
    from . import salary as salary_rules

    lines = plain.split("\n")
    salary = next((l.strip()[:80] for l in lines[:8] if salary_rules.parse(l)), "")
    return [TgJob(
        channel=channel, posted=when, title=title, company=company, location=_ru_format(fmt), salary=salary,
        url=_vacancy_link([u for u in _external_links(text.split(title, 1)[-1]) if channel not in u]),
        contact="",
        post_url=f"https://t.me/{channel}/{post_id}", text=plain,
    )]


def parse_pythonrabota(channel, post_id, when, raw):
    """@pythonrabota: '**Должность** | Смотреть вакансию ⟦telegra.ph⟧', ниже 'Формат:', 'Компания:', 'ЗП:'.

    Полный текст лежит на telegra.ph — его подтягивает collect() (fill_telegraph).
    """
    text = _to_text(raw)
    head = re.match(rf"\s*{B_OPEN}(.*?){B_CLOSE}", text, re.S)
    page = next((u for u in _external_links(text) if "telegra.ph/" in u), "")
    if not head or not page:
        return []
    plain = _plain(text)
    fields = {}
    for line in plain.split("\n"):
        key, sep, value = line.partition(":")
        if sep and len(key) < 15:
            fields[key.strip().lower()] = value.strip()
    handles = [h for h in _HANDLE_RX.findall(plain) if h.lower() != "@" + channel.lower()]
    return [TgJob(
        channel=channel, posted=when, title=_plain(head.group(1)), company=fields.get("компания") or "—",
        location=_ru_format(fields.get("формат", "")), salary=fields.get("зп", ""), url=page,
        contact=handles[0] if handles else "", post_url=f"https://t.me/{channel}/{post_id}", text=plain,
        full_text=False,
    )]


def _telegraph_text(node):
    if isinstance(node, str):
        return node
    inner = "".join(_telegraph_text(n) for n in node.get("children", []))
    return inner + "\n" if node.get("tag") in {"p", "li", "h3", "h4", "br", "blockquote"} else inner


def fill_telegraph(http, job):
    """Полный текст вакансии со страницы telegra.ph (публичный API getPage); уточняет локацию."""
    path = job.url.split("telegra.ph/", 1)[1].split("?")[0]
    try:
        r = http.get(f"https://api.telegra.ph/getPage/{path}", params={"return_content": "true"})
        content = r.json().get("result", {}).get("content", [])
    except (FetchError, ValueError) as e:
        log.warning("telegra.ph %s: %s", path, e)
        return
    body = "".join(_telegraph_text(n) for n in content).strip()
    if not body:
        return
    job.text, job.full_text = f"{job.title}\n{body}", True
    place = next((l.split(":", 1)[1].strip() for l in body.split("\n") if l.lower().startswith("локация:")), "")
    if place:
        place = re.sub(r"(?i)удал[её]нно", "Remote", place)
        job.location = _join_location([place[:60], *(x for x in job.location.split(", ") if x not in place)])


def parse_offer_inside(channel, post_id, when, raw):
    """@JobOfferInside: 'Должность ⟦ссылка на ATS⟧ в Компания', ниже строка с локацией и пересказ."""
    text = _to_text(raw)
    plain = _plain(text)
    lines = [l.strip() for l in plain.split("\n") if l.strip()]
    links = _external_links(text)
    if not lines or not links or " в " not in lines[0] or re.search(r"подборка|premium", lines[0], re.I):
        return []
    title, _, company = lines[0].rpartition(" в ")
    place = next((l.lstrip("🤩 ").strip() for l in lines[1:4] if l.startswith("🤩")), "")
    place = re.sub(r"(?i)где угодно", "Remote, Worldwide", place)
    place = re.sub(r"(?i)удал[её]нно", "Remote", place)
    place = re.sub(r"(?i)гибрид", "hybrid", place)
    return [TgJob(
        channel=channel, posted=when, title=title.strip(), company=company.strip() or "—",
        location=place, salary="", url=links[0], contact="",
        post_url=f"https://t.me/{channel}/{post_id}", text=plain, full_text=False,
    )]


def _field(plain, *names):
    """Значение строки «🏢 Компания: …» / «Формат: …»: первое из names, без эмодзи в начале."""
    for line in plain.split("\n"):
        key, sep, value = line.partition(":")
        if sep and re.sub(r"^\W+", "", key).strip().lower() in names:
            return value.strip()
    return ""


def parse_hiddengurus(channel, post_id, when, raw):
    """@hiddengurus: «**Должность**», «🏢 Компания:», «💰 ЗП:», «🌍 Формат:», «📩 Отклик: @…».

    Посты без строки «Компания:» (статьи про финансы и т. п.) пропускаются.
    """
    text = _to_text(raw)
    head = re.match(rf"\s*{B_OPEN}(.*?){B_CLOSE}", text, re.S)
    plain = _plain(text)
    company = _field(plain, "компания")
    if not head or not company:
        return []
    pay = _field(plain, "зп", "зарплата")
    handles = [h for h in _HANDLE_RX.findall(plain) if h.lower() != "@" + channel.lower()]
    links = _external_links(text)
    return [TgJob(
        channel=channel, posted=when, title=" ".join(_plain(head.group(1)).split()), company=company,
        # «Удалённо (РФ)»: _ru_format выбрасывает скобки, страну сохраняем словом
        location=_ru_format(re.sub(r"\((?:РФ|RU)\)", "Россия", _field(plain, "формат"))),
        salary=pay if _SALARY_RX.search(pay) else "",
        url=_vacancy_link(links), contact=handles[0] if handles else "",
        post_url=f"https://t.me/{channel}/{post_id}", text=plain,
    )]


def parse_opento(channel, post_id, when, raw):
    """@opento_dev: «**Должность / Компания**», следующая строка — локация (и зарплата).

    Дайджест — несколько таких пар в одном посте, у каждой ссылка на wantapply.com.
    """
    text = _to_text(raw)
    lines = text.split("\n")
    jobs = []
    for i, line in enumerate(lines):
        # компания — после последнего « / »: в должности бывает «Middle+ / Senior»
        m = re.match(rf"\s*{B_OPEN}(.+) / (.+?){B_CLOSE}\s*(?:⟦([^⟧]+)⟧)?", line)
        if not m:
            continue
        title, company = (" ".join(_plain(x).replace("⚡️", "").split()) for x in m.groups()[:2])
        place = _plain(lines[i + 1]) if i + 1 < len(lines) else ""
        pay_rx = r"(?:salary:\s*)?(?:от\s*)?(?:[$€]\s?\d[\d.,]*|\d[\d\s.,]*\s*(?:\$|€|usd|eur))"
        pay = re.search(rf"{pay_rx}(?:\s*-\s*{pay_rx})?", place, re.I)
        place = re.sub(rf"(?i)salary:.*|{pay_rx}.*", "", place).strip(" .,")
        # ссылки дайджеста приходят дважды экранированными («&amp;utm_…»)
        link = html.unescape(m.group(3) or _vacancy_link(_external_links(text)))
        digest = sum(bool(re.match(rf"\s*{B_OPEN}.+ / ", l)) for l in lines) > 1
        jobs.append(TgJob(
            channel=channel, posted=when, title=title, company=company, location=place,
            salary=pay.group(0).strip(" .") if pay else "", url=link or "", contact="",
            post_url=f"https://t.me/{channel}/{post_id}",
            text=f"{title} {company} {place}" if digest else _plain(text), full_text=not digest,
        ))
    return jobs



PARSERS = {
    "youritjob": parse_youritjob, "af_job_board": parse_af_job_board, "JobOfferInside": parse_offer_inside,
    "job_python": parse_job_python, "pythonrabota": parse_pythonrabota, "habr_career": parse_habr_digest,
    "career_sports": parse_career_sports,
    "zarubezhom_jobs": parse_zarubezhom,
    "relocatesme": parse_relocatesme,
    "jtbl_vacancy": parse_jtbl,
    "hiddengurus": parse_hiddengurus,
    "dev_connectablejobs": parse_zarubezhom,
    "opento_dev": parse_opento,
}
SHORT_POST_CHANNELS = {"af_job_board", "choicy_work", "JobOfferInside", "habr_career"}  # в посте нет полного текста
# каналы, где вакансии — пересылки из каналов компаний: пересланные посты не пропускаем
FORWARDS_OK_CHANNELS = {"zarubezhom_jobs"}
SALARY_POSTS: list = []  # все разобранные посты с указанной зарплатой — база для оценки вилок


def classify(job):
    """Проставляет label; False — вакансию не показываем."""
    if not filters.role_ok(job.title, job.text) or c.TG_EXCLUDE_RX.search(job.title):
        return False
    if not filters.full_time(job.title, "", job.text) or filters.residency_blocked(job.text, job.location):
        return False
    if HIDE_RUSSIA_ONLY and ru_only(f"{job.location}\n{job.text}"):
        return False  # удалёнка только из России
    if not c.PYTHON_RX.search(job.title):
        other = next((name for name, rx in c.TITLE_LANGS.items() if rx.search(job.title)), "")
        if other:
            job.marks.append(f"⚠️ Python not primary language ({other})")
    lang_marks, job.lang_required = language.analyze(job.title, job.text)
    job.marks += lang_marks
    if company_key(job.company) in geo.REFERRAL_COMPANIES:
        job.marks.append("🤝 возможна рефка")
    loc = job.location
    home = bool(c.HOME_RX.search(loc))
    remote = bool(c.REMOTE_RX.search(loc))
    if not remote and not home:
        # офис в другой стране подходит, только если переезд оплачивает работодатель
        if geo.RELOCATION_ENABLED and (geo.relocation_quote(job.text) or "relocation support" in loc):
            job.label = "🌍 офис в другой стране, работодатель помогает с релокацией"
            job.reloc_type = geo.RELOCATION  # verify.check не считает офис причиной отказа
            return True
        if geo.RELOCATION_ENABLED and re.search(r"hybrid|гибрид", loc, re.I):
            job.label = "❓ hybrid в другой стране — нужна релокация"
            return True
        return False  # onsite / hybrid в другой стране без помощи с переездом
    if home or c.BROAD_RX.search(loc):
        if not remote:
            job.label = f"✅ {c.HOME_COUNTRY} (не remote)"
        else:
            job.label = f"✅ remote + {c.HOME_COUNTRY} или worldwide/EU" if c.HOME_COUNTRY else "✅ remote worldwide/EU"
    elif c.LOCATION_NOISE_RX.sub("", c.REMOTE_RX.sub("", re.sub(r"#\w+|удал[её]нн?[а-яё]*", "", loc, flags=re.I))):
        if not geo.COUNTRY_BOUND_REMOTE:
            return False
        job.label = "❓ remote, привязан к другой стране"
    else:
        job.label = "❓ remote, регион не указан"
    return True


def _check_aggregator_link(http, job):
    job.marks.append("⚠️ через агрегатор — проверить, что вакансия жива")
    try:
        status = http.get(job.url).status_code
    except FetchError as e:
        log.warning("telegram: не удалось проверить ссылку %s: %s", job.url, e)
        job.marks.append("ссылка не проверена")
        return
    job.marks.append("ссылка открывается" if status == 200 else f"❌ ссылка не открывается (HTTP {status})")


def collect(http):
    """-> (вакансии, список каналов, которые не удалось прочитать)."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=c.TELEGRAM_MAX_AGE_DAYS)
    jobs, failed = {}, []
    for channel in c.TELEGRAM_CHANNELS:
        if progress.skip_requested():  # кнопка «Пропустить»: дальше идут уже прочитанные каналы
            break
        progress.item_started(f"@{channel}")
        parser = PARSERS.get(channel, parse_youritjob)
        try:
            posts = fetch_posts(http, channel, cutoff)
        except FetchError as e:
            log.warning("telegram @%s: %s", channel, e)
            failed.append(channel)
            progress.item_finished(f"@{channel}")
            continue
        found = 0
        for post_id, when, raw, forwarded in posts:
            if forwarded and channel not in FORWARDS_OK_CHANNELS:
                continue
            try:
                parsed = parser(channel, post_id, when, raw)
            except Exception:
                log.exception("telegram @%s/%s: не удалось разобрать пост", channel, post_id)
                continue
            for job in parsed:
                if channel in SHORT_POST_CHANNELS:
                    job.full_text = False
                if job.salary:
                    SALARY_POSTS.append(job)
                if not classify(job):
                    continue
                if "telegra.ph/" in job.url and not job.full_text:
                    fill_telegraph(http, job)
                    job.marks.clear()
                    if not classify(job):
                        continue
                found += 1
                # одна и та же вакансия в разных постах/каналах — оставляем самую свежую
                key = (company_key(job.company), re.sub(r"[^a-zа-яё0-9]", "", job.title.lower()))
                if key not in jobs or job.posted > jobs[key].posted:
                    jobs[key] = job
        log.info("telegram @%s: постов за %d дней — %d, подходящих вакансий — %d",
                 channel, c.TELEGRAM_MAX_AGE_DAYS, len(posts), found)
        progress.event("source", f"@{channel}: постов {len(posts)}, подходящих вакансий {found}")
        progress.item_finished(f"@{channel}")
    result = list(jobs.values())
    for job in result:
        if AGGREGATOR_HOSTS and any(host in job.url for host in AGGREGATOR_HOSTS):
            _check_aggregator_link(http, job)
    return result, failed
