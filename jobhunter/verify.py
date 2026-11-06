"""Проверка вакансии по полному тексту: локация, опыт, стек, вид работы.

Метка локации у источника ненадёжна («Spain only» при тексте «In-person in our
Chennai office»), поэтому окончательный статус ставится по тексту вакансии:

* job.reject / job.reject_quote — вакансия скрывается, причина попадает в раздел
  «Отброшено фильтрами»;
* job.manual_check — статус ❓: текст недоступен или регион remote не подтверждён;
  такие вакансии не попадают в «Топ для разбора»;
* job.penalties — штрафы к баллу (уровень, опыт, роль, язык).
"""
from __future__ import annotations

import re

from . import config as c
from . import filters, geo

MIN_TEXT_CHARS = 400  # короче — считаем, что текста вакансии нет
ONSITE_REASON = f"on-site вне {c.HOME_COUNTRY}" if c.HOME_COUNTRY else "on-site"
# remote только из России («на территории РФ») — как «US only» для русскоязычных источников:
# settings.toml [filters].hide_russia_only = true скрывает такие вакансии
HIDE_RUSSIA_ONLY = bool(geo.S.get("filters", {}).get("hide_russia_only", False))

_SENTENCE_RX = re.compile(r"[^\n.!?;•]+")
_SKILLS_LINE_RX = re.compile(r"\nSkills: .*$", re.S)

_CITIES = (
    "Chennai|Bangalore|Bengaluru|Mumbai|Hyderabad|Pune|Delhi|Gurgaon|Gurugram|Noida|Manila|Cebu|"
    "London|Manchester|Edinburgh|Dublin|Berlin|Munich|Hamburg|Frankfurt|Warsaw|Krakow|Kraków|Wroclaw|"
    "Paris|Amsterdam|Rotterdam|Madrid|Barcelona|Lisbon|Porto|Vienna|Zurich|Geneva|Stockholm|Copenhagen|"
    "Oslo|Helsinki|Tallinn|Riga|Vilnius|Prague|Budapest|Bucharest|Sofia|Athens|Istanbul|Tel Aviv|Dubai|"
    "Riyadh|Singapore|Hong Kong|Tokyo|Seoul|Sydney|Melbourne|Toronto|Vancouver|Montreal|New York|NYC|"
    "San Francisco|Seattle|Austin|Boston|Chicago|Denver|Los Angeles|Atlanta|Miami|Mexico City|"
    "São Paulo|Sao Paulo|Buenos Aires|Bogot[aá]|Cape Town|Lagos|Nairobi|Cairo|Limassol|Nicosia|Tbilisi|"
    "Yerevan|Almaty|Moscow|Kyiv|Minsk"
)
_COUNTRIES = (
    r"US|USA|U\.S\.A?\.?|United States|UK|U\.K\.|United Kingdom|EU|European Union|EEA|Canada|India|"
    "Germany|Poland|Spain|Portugal|Netherlands|France|Italy|Ireland|Switzerland|Austria|Sweden|"
    "Denmark|Norway|Finland|Estonia|Latvia|Lithuania|Czech(?:ia| Republic)|Hungary|Romania|Bulgaria|"
    "Greece|Turkey|Israel|UAE|Brazil|Mexico|Argentina|Colombia|Australia|New Zealand|Japan|Singapore|"
    "Philippines|Malaysia|LATAM|Latin America|North America|Americas|APAC|Asia|Africa|Cyprus|Georgia|"
    "Armenia|Kazakhstan|Ukraine"
)
_FOREIGN_PLACE_RX = re.compile(rf"\b(?:{_CITIES})\b|(?<![\w.])(?:{_COUNTRIES})(?![\w])")
_HOME_OR_OPEN_RX = re.compile(c.HOME_RX.pattern + r"|\beurope\b|\bemea\b|\bcet\b|anywhere|any country|worldwide", re.I)

# Фразы «работа из офиса»; "hybrid" — только в значении формата работы, не "hybrid cloud"
_ONSITE_RX = re.compile(
    r"\bin[\s-]person\b|\bon[\s-]?site\b|\bin (?:the|our) (?:[\w-]+ ){0,2}offices?\b|\boffice[\s-]based\b|"
    r"\bdays? (?:a|per|each) week (?:in|at|from) (?:the|our) (?:[\w-]+ )?office|"
    r"\bdays? (?:in|at) (?:the|our) (?:[\w-]+ )?office\b|"
    r"\bhybrid (?:work\w*|schedule|set-?up|working model|work model|arrangement|policy|format|basis)\b|"
    r"\(hybrid\)|\bhybrid in\b|\bhybrid[,:]? (?:\d|two|three|four)\b",
    re.I,
)
_CITY_WORKPLACE_RX = re.compile(
    rf"(?:\b(?:in|at|from|out of) our (?P<a>{_CITIES}) (?:office|hub|hq)|"
    rf"\b(?:based|located|on[\s-]?site|in[\s-]person) (?:in|at) (?:the |our )?(?P<b>{_CITIES})\b|"
    rf"\b(?P<c>{_CITIES})[\s-]based (?:role|position|candidates?|engineers?|developers?)\b|"
    rf"\b(?:role|position|job) is (?:based |located )?in (?:the |our )?(?P<d>{_CITIES})\b)"
)
_ABOUT_YOU_RX = re.compile(r"\b(?:you|your|candidates?|role|position|must|required|expected|this job)\b", re.I)
# Смягчители: офис упомянут, но приходить не обязательно
_SOFTENER_RX = re.compile(
    r"optional|not (?:required|mandatory|necessary)|no (?:need|requirement|obligation|expectation)|"
    r"if you (?:want|prefer|like|wish|choose|live)|option(?:ally)? to|can also|(?:or|and) (?:fully |100% )?remote|"
    r"remote(?:ly)?(?:,| or| and|/)|remote[\s-]first|fully remote|100% remote|occasional\w*|"
    r"remote\s*\([^)]*\)\s*or|contractor arrangement|\bor\s+(?:[A-Z][\w-]*\s+){0,2}on-?site|"
    r"once (?:a|per|every) (?:quarter|year|month)|times? (?:a|per) year|off-?sites?|meet-?ups?|"
    r"team (?:gatherings?|events?|retreats?)|retreat|onboarding|are welcome|welcome to|depending on|"
    r"hybrid (?:or|/) remote|on-?site interviews?|on-?call|visit\w*|travel\w*|whether you|"
    r"face[\s-]to[\s-]face|in[\s-]person (?:connection|time|events?|meetings?|gatherings?)|get[\s-]togethers?|"
    r"summits?|conferences?|all[\s-]hands|company[\s-]wide|gym|lunch|perks?|benefits?|"
    r"\bmeet(?:ing|s)? in[\s-]person|\bmet in[\s-]person|interview\w*|video calls?|co-?working|"
    r"every (?:\w+ ){0,4}(?:months?|quarters?|years?)|quarterly|annual\w*",
    re.I,
)
_RESIDENCY_RX = re.compile(
    r"must (?:be (?:based|located|resident|residing)|reside|live)(?: in)?|right to work in|"
    r"eligible to work in|authori[sz]ed to work in|work authori[sz]ation (?:in|for)",
    re.I,
)
# Текст подтверждает remote без привязки к стране
_REMOTE_ANYWHERE_RX = re.compile(
    r"work(?:ing)? from anywhere|anywhere in (?:the )?(?:world|europe|emea)|remote(?:ly)? (?:with)?in (?:europe|emea)|"
    r"across (?:europe|emea)|fully distributed|globally distributed|world[\s-]?wide|location[\s-]agnostic|"
    r"any (?:country|location|time ?zone)|european time ?zones?|\bcet\b|\bemea\b|remote[\s,-]+(?:europe|global)",
    re.I,
)

_YEARS_RX = re.compile(
    r"\b(\d{1,2})\s?\+\s*(?:years|yrs|лет)|\b(\d{1,2})\s+(?:or more|plus)\s+years|\bот\s+(\d{1,2})\s*\+?\s*лет|"
    r"(?:minimum|at least|over|more than)\s+(?:of\s+)?(\d{1,2})\s+years(?=['’]?\s+(?:of\s+)?(?:[\w-]+\s+){0,3}experience)",
    re.I,
)
_EXPERIENCE_CONTEXT_RX = re.compile(
    r"experience|engineer|develop|software|programming|industry|back[\s-]?end|python|professional|опыт|"
    r"разработ|коммерческ|в роли|работы", re.I
)
_NOT_A_JOB_RX = re.compile(
    r"\bai trainer\b|train AI systems|per[\s-]task\b|output[\s-]based|\bannotat(?:or|ion)s?\b|data label", re.I
)
HIDE_LANGUAGES = {"Go", "Java", "Kotlin", "Ruby", "PHP", "C#/.NET", ".NET", "Rust"}
_REQUIRED_LANG_RX = re.compile(
    r"(?:experience|proficien\w+|expert\w*|strong|fluen\w+|years|skills?|knowledge|written)[^.\n]{0,40}?"
    r"(?:\b[Gg]olang\b|\bGo\b(?!-to|\s+(?:to|live|beyond|above))|\bJava\b(?!\s?Script)|\bKotlin\b|"
    r"\bRuby\b|\bPHP\b|\bRust\b|\bC#|(?<![\w])\.NET\b|\bTypeScript\b|\bNode\.?js\b)"
)
_OPS_TEXT_RX = re.compile(
    r"terraform|ansible|pulumi|helm|kubernetes|\bk8s\b|on[\s-]call|incident|\bsre\b|devops|"
    r"infrastructure as code|\biac\b|ci/cd pipelines?|observability|monitoring|alerting|provisioning|"
    r"cloud infrastructure|networking|load balanc\w+|capacity planning|runbooks?|uptime|\bslos?\b",
    re.I,
)
_PRODUCT_TEXT_RX = re.compile(
    r"\bapis?\b|product|feature|customers?|users?|business logic|domain|backend services?|microservices?|"
    r"data model|database schema|endpoints?|integrations?|payments?|checkout|platform for (?:our )?customers",
    re.I,
)
_LEVEL_RX = re.compile(r"\b(?:staff|principal|head|director)\b", re.I)
_ROLE_PENALTY_RX = c.ROLE_PENALTY_RX
_CORE_TITLE_RX = re.compile(r"back[\s-]?end|software|б[эе]к[\s-]?енд", re.I)


def quote(text: str, start: int, end: int, max_words: int = 12) -> str:
    """Цитата не длиннее max_words слов вокруг фрагмента text[start:end]."""
    before = text[:start].split()[-(max_words // 3):]
    phrase = text[start:end].split()
    after = text[end:].split()[: max(0, max_words - len(before) - len(phrase))]
    return " ".join([*before, *phrase, *after])[:160]


def core_text(text: str) -> str:
    """Текст вакансии без строки навыков, которую скрипт дописывает сам."""
    return _SKILLS_LINE_RX.sub("", text or "").strip()


def label_kind(location: str, title: str) -> str:
    """'home' (домашняя страна) | 'broad' (Worldwide/Europe/EMEA/CIS…) | 'bare' (просто Remote или страна)."""
    if c.HOME_RX.search(f"{location} {title}"):
        return "home"
    if c.BROAD_RX.search(location) or c.TITLE_BROAD_RX.search(title):
        return "broad"
    return "bare"


def _onsite(text: str, home_label: bool) -> tuple[int, int] | None:
    """Позиция фразы об обязательной работе из офиса вне домашней страны либо None."""
    for m in _SENTENCE_RX.finditer(text):
        sentence, offset = m.group(), m.start()
        if c.HOME_RX.search(sentence) or _SOFTENER_RX.search(sentence):
            continue
        city = _CITY_WORKPLACE_RX.search(sentence)
        if city and _ABOUT_YOU_RX.search(sentence):
            return offset + city.start(), offset + city.end()
        hit = _ONSITE_RX.search(sentence)
        if not hit:
            continue
        # у вакансии в домашней стране «on-site» допустим — мешает только чужой город рядом
        if home_label and not _FOREIGN_PLACE_RX.search(sentence):
            continue
        return offset + hit.start(), offset + hit.end()
    return None


def _residency(text: str) -> tuple[int, int] | None:
    """«must be based in / right to work in <чужая страна>» либо None."""
    for m in _RESIDENCY_RX.finditer(text):
        window = text[m.end(): m.end() + 90]
        place = _FOREIGN_PLACE_RX.search(window)
        if place and not _HOME_OR_OPEN_RX.search(window):
            return m.start(), m.end() + place.end()
    return None


def _years(text: str) -> tuple[int, tuple[int, int]] | None:
    """Наибольшее требование по годам опыта и его позиция в тексте."""
    best = None
    for m in _YEARS_RX.finditer(text):
        n = int(next(g for g in m.groups() if g))
        if n > 20 or not _EXPERIENCE_CONTEXT_RX.search(text[m.end(): m.end() + 50]):
            continue
        if best is None or n > best[0]:
            best = (n, (m.start(), m.end()))
    return best


def _required_language(title: str, text: str) -> tuple[int, int] | None:
    """Другой язык заявлен обязательным, а Python в тексте почти не упоминается."""
    if c.PYTHON_RX.search(title) or len(c.PYTHON_RX.findall(text)) > 1:
        return None
    for m in _SENTENCE_RX.finditer(text):
        hit = _REQUIRED_LANG_RX.search(m.group())
        if hit and not c.PYTHON_RX.search(m.group()):
            return m.start() + hit.start(), m.start() + hit.end()
    return None


# remote / работа только из России: «удалённо на территории РФ», «гражданство РФ»
_RU_ONLY_RX = re.compile(
    r"(?:территори\w*|пределах|находит\w*|проживани\w*|гражданств\w*|резидент\w*|только из|только в)\s+"
    r"(?:в\s+)?(?:РФ|России|Российской|Москв|Санкт|СПб)",
    re.I,
)
ABROAD_OK_RX = re.compile(r"за пределами (?:РФ|России)|из любой (?:точки|страны)|не обязательно в (?:РФ|России)", re.I)


def ru_only(text):
    """Работа возможна только из России (и не сказано, что можно из-за рубежа)."""
    return bool(_RU_ONLY_RX.search(text)) and not ABROAD_OK_RX.search(text)



def check(job, text: str, full_text: bool) -> None:
    """Проставляет job.reject / job.manual_check / job.penalties по тексту вакансии.

    job — Job или TgJob; text — описание; full_text — описание получено целиком
    (для источников, которые отдают только превью, — False).
    """
    title, location = job.title, job.location or ""
    body = core_text(text)
    has_text = full_text and len(body) >= MIN_TEXT_CHARS
    kind = label_kind(location, title)
    job.penalties, job.manual_check = [], ""

    def reject(reason: str, span: tuple[int, int] | None = None, source: str = body) -> None:
        job.reject = reason
        job.reject_quote = quote(source, *span) if span else ""

    # --- remote только из России (settings.toml [filters].hide_russia_only)
    m = _RU_ONLY_RX.search(body) if HIDE_RUSSIA_ONLY else None
    if m and not ABROAD_OK_RX.search(body):
        return reject("удалёнка только из России", m.span())

    # --- вид работы
    m = _NOT_A_JOB_RX.search(f"{title}\n{body}")
    if m:
        return reject("AI trainer / сдельная разметка", m.span(), f"{title}\n{body}")

    # --- локация по тексту
    # вакансия с релокацией в целевую страну: офис там — это ожидаемо, а не причина отказа
    relocation = getattr(job, "reloc_type", "") in (geo.RELOCATION, geo.UNCONFIRMED)
    span = None if relocation else _onsite(body, home_label=(kind == "home"))
    if span and geo.RELOCATION_ENABLED:
        # «Cyprus — Onsite, employment contract with relocation support»: офис в другой стране + переезд
        # за счёт работодателя — вакансия для 🌍, а не отказ (даже если источник пометил её remote).
        # Страна — сначала из названия («… - Relocation to Cyprus»), потом из текста.
        reloc_quote = geo.relocation_quote(body)
        if reloc_quote and not any(rx.search(f"{location} {title}") for rx in geo.EXCLUDE_RX):
            job.reloc_type, job.reloc_quote = geo.RELOCATION, reloc_quote
            job.reloc_country = geo.detect_country(title) or geo.detect_country(body[:3000]) or geo.OTHER
            relocation, span = True, None
    if span:
        return reject(ONSITE_REASON, span)
    span = None if relocation else _residency(body)
    if span and kind != "home":
        return reject("привязка к стране: нужно жить / иметь право работать в другой стране", span)

    # --- опыт
    experience = (getattr(job, "extra", None) or {}).get("enrichment", {}).get("experience_years_min") or 0
    years = _years(body)
    # от 8 лет — не приговор: вакансия остаётся, но со штрафом (ниже в списках, не в топе)
    if (years and years[0] >= 8) or experience >= 8:
        job.penalties.append("experience_8")
    elif experience == 7 or (years and years[0] == 7):
        job.penalties.append("experience_7")

    # --- стек
    lang = filters.language_mismatch_text(title, body)
    if lang in HIDE_LANGUAGES:
        rx = c.TITLE_LANGS.get(lang) or c.OTHER_LANGS.get(lang)
        m = rx.search(f"{title}\n{body}") if rx else None
        return reject(f"основной язык — {lang}", m.span() if m else None, f"{title}\n{body}")
    m = c.JVM_SPRING_RX.search(body)
    if m:
        return reject("JVM / Spring в требованиях", m.span())
    span = _required_language(title, body)
    if span:
        return reject("обязательный язык не Python", span)
    if lang:
        job.penalties.append("language")

    # --- штрафы за уровень и роль
    if _LEVEL_RX.search(title):
        job.penalties.append("level")
    if _ROLE_PENALTY_RX.search(title) and not _CORE_TITLE_RX.search(title):
        job.penalties.append("role")
    # название «Software Engineer», а по тексту — Terraform, Kubernetes, on-call и инциденты
    ops = len(_OPS_TEXT_RX.findall(body))
    product = len(_PRODUCT_TEXT_RX.findall(body))
    if ops >= 6 and ops >= 2 * max(product, 1):
        job.penalties.append("ops_heavy")

    # --- статус
    if not has_text:
        job.manual_check = "текст вакансии недоступен"
    elif kind == "bare" and not getattr(job, "reloc_type", "") and not _REMOTE_ANYWHERE_RX.search(body):
        job.manual_check = "регион remote не указан ни в метке, ни в тексте"
