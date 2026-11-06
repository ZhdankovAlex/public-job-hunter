"""Отбор вакансий: ключевые слова, локация, основной язык."""
import re

from . import config as c
from . import geo, language


def role_ok(title, text, relaxed=False):
    """Единый фильтр роли для всех разделов (ATS, агрегаторы, Telegram).

    relaxed — для приоритетных компаний: подходит любая инженерная роль с python в тексте.
    """
    if c.ROLE_EXCLUDE_RX.search(title) or c.TITLE_EXCLUDE_RX.search(title):
        return False
    full = f"{title}\n{text}"
    if c.OPS_ROLE_RX.search(title) and not c.CORE_TITLE_RX.search(title):
        return bool(c.PYTHON_RX.search(full))  # DevOps / SRE — категория 3, только с Python
    if relaxed and c.PRIORITY_ROLE_RX.search(title):
        return bool(c.PYTHON_RX.search(full))
    if c.ROLE_CORE_RX.search(title):
        return bool(c.KEYWORD_RX.search(full) or c.TG_KEYWORD_RX.search(full))
    if c.ROLE_AIML_RX.search(title) or c.ROLE_PLATFORM_RX.search(title) or c.ROLE_PENALTY_RX.search(title):
        return bool(c.PYTHON_RX.search(full))
    return False


def title_candidate(title):
    """Грубая проверка по одному названию — стоит ли открывать страницу вакансии."""
    if c.ROLE_EXCLUDE_RX.search(title) or c.TITLE_EXCLUDE_RX.search(title):
        return False
    if c.OPS_ROLE_RX.search(title) and not c.CORE_TITLE_RX.search(title):
        return True
    return bool(
        c.ROLE_CORE_RX.search(title) or c.ROLE_AIML_RX.search(title) or c.ROLE_PLATFORM_RX.search(title)
        or c.ROLE_PENALTY_RX.search(title)
    )


def full_time(title, employment, text):
    """Штат или контракт на полный день; part-time, фриланс, почасовка и стажировки — нет."""
    if c.NOT_FULL_TIME_RX.search(f"{title} {employment}") or c.NOT_A_JOB_TITLE_RX.search(title):
        return False
    return not (c.HOURLY_PAY_RX.search(text) or c.NOT_A_JOB_TEXT_RX.search(text))


def residency_blocked(text, location):
    """«must reside in …» / «not providing relocation», если рядом нет домашней страны или EU."""
    if c.RESIDENCY_OK_RX.search(location):
        return False
    for m in c.RESIDENCY_RX.finditer(text):
        if not c.RESIDENCY_OK_RX.search(text[max(0, m.start() - 60): m.end() + 120]):
            return True
    return False


def no_support(text):
    """В тексте прямо сказано, что релокации или визовой поддержки нет."""
    return bool(c.NO_SUPPORT_RX.search(text))


def keyword_match(job):
    relaxed = geo.company_key(job.company) in geo.PRIORITY_COMPANIES
    return role_ok(job.title, job.description, relaxed)


def location_verdict(job):
    """'ok' | 'check' (remote, но привязан к стране/городу) | None (не подходит)."""
    loc = job.location or ""
    text = f"{loc} {job.title}"
    if c.HOME_RX.search(text):
        return "ok"
    if not (job.remote or c.REMOTE_RX.search(text)):
        return None
    # "Remote - CA", "U.S. Anywhere", "NAMER" — привязка к США, если рядом нет Европы
    if c.US_BOUND_RX.search(text) and not c.LOCATION_EUROPE_RX.search(loc):
        return None if c.NON_EU_RX.search(loc) else "check"
    if c.BROAD_RX.search(loc) or c.TITLE_BROAD_RX.search(job.title):
        return "ok"
    if not c.LOCATION_NOISE_RX.sub("", loc):
        return "ok"  # просто "Remote", без региона
    if c.NON_EU_RX.search(loc):
        return None
    return "check"


def _python_is_secondary(desc, match):
    """Упоминание Python стоит в nice-to-have контексте?"""
    line_start = desc.rfind("\n", 0, match.start()) + 1
    line_end = desc.find("\n", match.end())
    line = desc[line_start: line_end if line_end != -1 else len(desc)]
    if c.NICE_TO_HAVE_RX.search(line):
        return True
    before = desc[max(0, match.start() - 400): match.start()]
    nice = list(c.NICE_TO_HAVE_RX.finditer(before))
    if not nice:
        return False
    return not c.REQUIRED_RX.search(before, nice[-1].end())


def language_mismatch(job):
    """Имя языка, который выглядит основным вместо Python, либо ''."""
    return language_mismatch_text(job.title, job.description)


_REQ_START_RX = re.compile(
    r"^\W*(?:requirements|qualifications|what we look for|what you(?:'ll)? (?:need|bring)|you (?:have|bring)|"
    r"must[- ]haves?|about you|who you are|требования|мы ждём|что мы ждём|ожидаем)\b.*$",
    re.I | re.M,
)
_REQ_END_RX = re.compile(
    r"^\W*(?:nice[- ]to[- ]have|bonus|plus|preferred|why |benefits|we offer|what we offer|perks|"
    r"будет плюсом|плюсом будет|условия|мы предлагаем|что мы предлагаем)\b.*$",
    re.I | re.M,
)


_PLUS_LINE_RX = re.compile(
    r"плюсом|будет плюс|преимуществ|nice[- ]to[- ]have|\bplus\b|\bbonus\b|preferred|desirable|advantage",
    re.I,
)


def _required_lang(desc):
    """Другой язык, если блок требований называет его и не называет Python, иначе ''.

    Пример: в задачах «code in C++ and Python», а в «What We Look For» — только C++.
    """
    start = _REQ_START_RX.search(desc)
    if not start:
        return ""
    rest = desc[start.end():]
    end = _REQ_END_RX.search(rest)
    section = rest[: end.start() if end else 2000]
    if c.PYTHON_RX.search(section):
        return ""
    # «Node.js или Rust будут плюсом» внутри требований — не обязательный язык
    section = "\n".join(line for line in section.splitlines() if not _PLUS_LINE_RX.search(line))
    if not section.strip():
        return ""
    # C++ проверяется только здесь: в тексте ML-вакансий он часто стоит рядом с Python как плюс
    langs = {**c.OTHER_LANGS, "C++": c.TITLE_LANGS["C++"]}
    found = [name for name, rx in langs.items() if rx.search(section)]
    return found[0] if len(found) == 1 else ""


def language_mismatch_text(title, desc):
    """То же по названию и тексту (для вакансий без поля description — Telegram)."""
    if c.PYTHON_RX.search(title):
        return ""
    for name, rx in c.TITLE_LANGS.items():
        if rx.search(title):
            return name
    required = _required_lang(desc)
    if required:
        return required
    counts = {name: len(rx.findall(desc)) for name, rx in c.OTHER_LANGS.items()}
    top = max(counts, key=counts.get)
    n = counts[top]
    if n == 0:
        return ""
    py = list(c.PYTHON_RX.finditer(desc))
    if not py:
        return top if n >= 2 else ""
    # "Python, Go, or Java" — Python перечислен наравне с другим языком, это не «чужой» стек
    for m in py:
        start = max(desc.rfind(ch, 0, m.start()) for ch in "\n.;•") + 1
        ends = [i for i in (desc.find(ch, m.end()) for ch in "\n.;•") if i != -1]
        sentence = desc[start: min(ends) if ends else len(desc)]
        if c.OTHER_LANGS[top].search(sentence):
            return ""
    if n >= 3 and n >= 3 * len(py):
        return top
    if n >= len(py) and all(_python_is_secondary(desc, m) for m in py):
        return top
    return ""


def _reject(job, reason, quote=""):
    """Запоминает причину отказа (для раздела «Отброшено фильтрами») и возвращает False."""
    job.reject, job.reject_quote = reason, quote
    return False


def apply(job):
    """True, если вакансия проходит; заодно проставляет пометки.

    Если вакансия подходит по роли, но отсеяна другим правилом, причина остаётся в job.reject.
    """
    from . import verify  # verify импортирует этот модуль

    if not job.url or not keyword_match(job):
        return False
    if not full_time(job.title, job.employment, job.description):
        m = (c.NOT_FULL_TIME_RX.search(f"{job.title} {job.employment}") or c.HOURLY_PAY_RX.search(job.description)
             or c.NOT_A_JOB_TEXT_RX.search(job.description) or c.NOT_A_JOB_TITLE_RX.search(job.title))
        return _reject(job, "не full-time: part-time / фриланс / почасовая или сдельная оплата", m.group() if m else "")
    verdict = location_verdict(job)
    if verdict != "ok":
        if no_support(job.description):
            return _reject(job, "локация по метке источника: не remote и не домашняя страна", job.location[:80])
        # привязана к стране: возможно, это релокация / remote в целевой стране
        is_remote = bool(job.remote or c.REMOTE_RX.search(f"{job.location} {job.title}"))
        in_target = geo.RELOCATION_ENABLED and geo.classify(job, is_remote)
        if not in_target and (verdict is None or not geo.COUNTRY_BOUND_REMOTE):
            return _reject(job, "локация по метке источника: не remote и не домашняя страна", job.location[:80])
    job.location_check = verdict == "check"
    full_text = job.source not in c.PREVIEW_ONLY_SOURCES
    verify.check(job, job.description, full_text)
    if job.reject:
        return False
    job.lang_marks, job.lang_required = language.analyze(job.title, job.description)
    job.mismatch_lang = language_mismatch(job)
    job.language_mismatch_warning = bool(job.mismatch_lang)
    return True
