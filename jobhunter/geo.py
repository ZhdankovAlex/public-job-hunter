"""Релокация и remote с привязкой к стране (страны и компании — в settings.toml)."""
import re
import tomllib

from . import config
from .keys import company_key

S = tomllib.loads(config.SETTINGS_FILE.read_text(encoding="utf-8"))
COUNTRIES = list(S.get("relocation_countries", []))
ANY_COUNTRY = bool(S.get("any_country", False))
OTHER = "Other"

# Типы в порядке показа внутри страны: сначала подтверждённая релокация
RELOCATION = "релокация"
UNCONFIRMED = "релокация (не подтверждена)"
REMOTE_OK = "remote: допускает работу вне страны"
REMOTE_UNCLEAR = "remote: неясно, можно ли вне страны"
RESIDENTS_ONLY = "❌ только резиденты"
TYPE_ORDER = [RELOCATION, UNCONFIRMED, REMOTE_OK, REMOTE_UNCLEAR, RESIDENTS_ONLY]


def _names_rx(names):
    names = sorted(names, key=len, reverse=True)
    return re.compile(r"(?<!\w)(?:" + "|".join(re.escape(n) for n in names) + r")(?!\w)", re.I)


_PLACES = S.get("places", {})
COUNTRY_RX = {c: _names_rx([c, *_PLACES.get(c, [])]) for c in COUNTRIES}
EXCLUDE_RX = [_names_rx([c, *_PLACES.get(c, [])]) for c in S.get("exclude_countries", [])]
RELOCATION_COMPANIES = {
    company_key(n) for names in S.get("relocation_companies", {}).values() for n in names
}
RELOCATION_ENABLED = bool(S.get("relocation_enabled", True))
COUNTRY_BOUND_REMOTE = bool(S.get("country_bound_remote", True))
PRIORITY_COMPANIES = {company_key(n) for n in S.get("priority_companies", [])}
# Крупные компании (bigtech.py) пишут о релокации в тексте вакансии редко, хотя обычно её дают:
# их вакансии в этих странах идут в 🌍 как «релокация (не подтверждена)».
BIGTECH_SOURCES = {"microsoft", "workday", "oracle", "amazon"}
BIGTECH_HUBS_RX = _names_rx(S.get("bigtech", {}).get("relocation_hubs", []) or ["-"])
# компании с обычных ATS, к которым то же правило (крупные трейдинговые и т.п.)
BIGTECH_COMPANIES = {company_key(n) for n in S.get("bigtech", {}).get("companies", [])}
REFERRAL_COMPANIES = {company_key(n) for n in S.get("referral_companies", [])}

# "Georgia" — страна, а не штат США
_GEORGIA_CITY_RX = re.compile(r"tbilisi|batumi|kutaisi", re.I)
_GEORGIA_COUNTRY_CONTEXT_RX = re.compile(r"tbilisi|batumi|kutaisi|caucasus|\bGEL\b")
_US_CONTEXT_RX = re.compile(r"\bUSA?\b|U\.S\.|United States|Atlanta|\bGA\b")

_SENTENCE_SPLIT_RX = re.compile(r"[\n.!?;•]+")
# Только предложение помощи: "relocation package", "visa sponsorship", "we sponsor visas".
# "eager to relocate", "relocation to Tokyo after onboarding" сюда не попадают.
_RELOCATION_RX = re.compile(
    r"relocation\W+(?:\w+\W+){0,2}?(?:support|package|assistance|bonus|allowance|budget|help|benefits?)|"
    r"(?:support|help|assist\w*|cover\w*|pay\w*)\W+(?:\w+\W+){0,3}?relocat\w+|"
    r"visa\W+(?:and\W+\w+\W+)?(?:sponsor\w*|support|assistance)|"
    r"sponsor\w*\W+(?:\w+\W+){0,3}?(?:visas?|work permits?)\b|"
    r"work permit\W+(?:sponsor\w*|support|assistance)|blue card|highly skilled migrant|"
    r"help you (?:to )?(?:move|relocate)|"
    r"помо\w+ с (?:переездом|релокацией|визой)|релокационн\w+ (?:пакет|бонус)|"
    r"визов\w+ поддержк\w+|оплат\w+ (?:переезд\w*|релокац\w+)",
    re.I,
)
# "we do not offer relocation" — отрицание прямо перед фразой
_NEGATION_RX = re.compile(
    r"\b(?:no|not|unable|cannot|can't|don't|doesn't|won't|without|unfortunately)\b|\bне\b|\bбез\b", re.I
)
_NEGATION_AFTER_RX = re.compile(
    r"\W*(?:\w+\W+){0,3}?(?:not|never|unavailable|нет|не)\b", re.I
)
_RELOCATION_TARGET_RX = re.compile(r"\bto\s+(?:the\s+)?([A-Z][\w-]+(?:\s[A-Z][\w-]+)?)")
_ANYWHERE_RX = re.compile(
    r"work from anywhere|anywhere in (?:the )?(?:world|europe|eu|emea)|"
    r"remote (?:with)?in (?:the )?(?:eu|emea|europe)|across (?:the )?(?:eu|emea|europe)|"
    r"any country|from any (?:location|country)|fully distributed",
    re.I,
)
_RESIDENT_RX = re.compile(
    r"must (?:be|reside|live) (?:\w+ )?(?:based|located|resident|residing)? ?in|based in {c}|"
    r"{c} only|only (?:in|from) {c}|right to work in|eligible to work in|"
    r"authori[sz]ed to work in|residen(?:t|ce|cy) (?:in|of)|valid work permit",
)
_SALARY_RX = re.compile(
    r"(?:[€$£]\s?\d{2,3}(?:[.,]\d{3})*\s?[kK]?\s?(?:[-–—]|to)\s?[€$£]?\s?\d{2,3}(?:[.,]\d{3})*\s?[kK]?"
    r"|\d{2,3}(?:[.,]\d{3})*\s?[kK]?\s?(?:[-–—]|to)\s?\d{2,3}(?:[.,]\d{3})*\s?[kK]?\s?(?:€|EUR|USD|GBP))"
)


def detect_country(location, description=""):
    """Целевая страна по локации вакансии (город или страна) либо ''."""
    for country, rx in COUNTRY_RX.items():
        if not rx.search(location):
            continue
        if country == "Georgia" and not _GEORGIA_CITY_RX.search(location):
            if _US_CONTEXT_RX.search(location):
                continue
            if not _GEORGIA_COUNTRY_CONTEXT_RX.search(description) and _US_CONTEXT_RX.search(description):
                continue
        return country
    return ""


def relocation_quote(text, country="", max_words=12):
    """Цитата (≤12 слов вокруг фразы) с предложением релокации В эту страну либо ''."""
    for m in _RELOCATION_RX.finditer(text):
        if _NEGATION_RX.search(text[max(0, m.start() - 45): m.start()]):
            continue
        if _NEGATION_AFTER_RX.match(text[m.end(): m.end() + 60]):
            continue
        # "relocation support to Tokyo" — релокация не туда
        target = _RELOCATION_TARGET_RX.match(text[m.end(): m.end() + 40].lstrip())
        if target and country in COUNTRY_RX and not COUNTRY_RX[country].search(target.group(1)):
            continue
        before = text[: m.start()].split()[-(max_words // 3):]
        phrase = m.group().split()
        after = text[m.end():].split()[: max(0, max_words - len(before) - len(phrase))]
        quote = " ".join([*before, *phrase, *after])
        return re.split(r"[\n•]", quote)[0].strip() if "\n" not in m.group() else quote
    return ""


def role_fits(job):
    """Раздел 🌍 — только инженерные Python-роли."""
    if config.RELOC_EXCLUDE_TITLE_RX.search(job.title):
        return False
    text = f"{job.title}\n{job.description}"
    if config.RELOC_NEEDS_PYTHON_TITLE_RX.search(job.title):
        return bool(config.PYTHON_RX.search(text))
    return bool(config.KEYWORD_RX.search(text))


def classify(job, is_remote):
    """Заполняет job.reloc_* для вакансии, привязанной к стране. True — вакансия берётся."""
    if not role_fits(job):
        return False
    country = detect_country(job.location, job.description)
    quote = relocation_quote(job.description, country)
    bigtech = getattr(job, "source", "") in BIGTECH_SOURCES or company_key(job.company) in BIGTECH_COMPANIES
    bigtech_hub = bigtech and bool(BIGTECH_HUBS_RX.search(job.location))
    if not country:
        if not ANY_COUNTRY or any(rx.search(job.location) for rx in EXCLUDE_RX):
            return False
        if quote:
            job.reloc_country, job.reloc_type, job.reloc_quote = OTHER, RELOCATION, quote
            return True
        if bigtech_hub:
            job.reloc_country, job.reloc_type, job.reloc_quote = OTHER, UNCONFIRMED, ""
            return True
        return False
    if quote:
        kind = RELOCATION
    elif is_remote:
        resident_rx = re.compile(_RESIDENT_RX.pattern.replace("{c}", re.escape(country)), re.I)
        if _ANYWHERE_RX.search(job.description):
            kind = REMOTE_OK
        elif resident_rx.search(job.description):
            kind = RESIDENTS_ONLY
        else:
            kind = REMOTE_UNCLEAR
    elif company_key(job.company) in RELOCATION_COMPANIES or bigtech_hub:
        kind = UNCONFIRMED
    else:
        return False
    job.reloc_country, job.reloc_type, job.reloc_quote = country, kind, quote
    if not job.salary:
        m = _SALARY_RX.search(job.description)
        job.salary = m.group().strip() if m else ""
    return True
