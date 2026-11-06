import html
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class Company:
    name: str
    score: int = 0  # приоритет компании (0–3) из companies.txt
    note: str = ""


@dataclass
class Job:
    company: str
    title: str
    location: str
    url: str
    source: str
    posted: datetime | None = None
    description: str = ""
    remote: bool | None = None
    score: int = 0  # приоритет компании (0–3) из companies.txt
    note: str = ""
    # заполняется фильтрами
    location_check: bool = False
    language_mismatch_warning: bool = False
    mismatch_lang: str = ""
    applied_status: str = ""  # статусы откликов в эту компанию (applications_manual.toml)
    salary: str = ""
    employment: str = ""  # тип занятости, как его отдал источник (Full-time, Contract, …)
    relevance: int = 0  # балл по profile.toml
    matched_skills: list = field(default_factory=list)
    # релокация / remote с привязкой к стране (geo.py)
    reloc_country: str = ""
    reloc_type: str = ""
    reloc_quote: str = ""
    # языковой фильтр (language.py)
    lang_marks: list = field(default_factory=list)
    lang_required: bool = False
    referral: bool = False
    staffing: str = ""  # причина, по которой вакансия вынесена в Staffing-приложение
    history_marks: list = field(default_factory=list)
    app_status: str = ""  # статус отклика в эту компанию по почте / Telegram (applications_report)
    is_new_today: bool = False
    flags: list = field(default_factory=list)
    # проверка по полному тексту (verify.py)
    reject: str = ""  # причина, по которой вакансия скрыта
    reject_quote: str = ""  # цитата из текста, на которой сработало правило
    manual_check: str = ""  # статус ❓: почему нужна ручная проверка
    penalties: list = field(default_factory=list)  # level / experience_7 / role / language
    salary_note: str = ""  # вилка: указанная или оценка по базе (salary.py)
    levels_note: str = ""  # медиана по стране вакансии с levels.fyi (levels.py)
    levels_url: str = ""
    # сопоставление с резюме (cv_match.py)
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
    tier: int = 4  # категория: 1 backend, 2 ML, 3 DevOps/платформа, 4 прочее (tiers.py)
    sources: list = field(default_factory=list)  # все источники, где встретилась вакансия
    extra: dict = field(default_factory=dict)  # исходные поля freehire (enrichment, collections, …)


def norm_name(name):
    return re.sub(r"[^a-z0-9а-яё]", "", name.lower())


def html_to_text(raw):
    if not raw:
        return ""
    text = html.unescape(raw)  # Greenhouse отдаёт HTML дважды экранированным
    text = re.sub(r"(?i)<(?:br|/p|/li|/h\d|/div|/ul)[^>]*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"[ \t\xa0]+", " ", text).strip()


def parse_date(value):
    """ISO-строка, unix-секунды или миллисекунды -> aware datetime (UTC)."""
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)):
            if value > 1e11:
                value /= 1000
            return datetime.fromtimestamp(value, tz=timezone.utc)
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, OSError, OverflowError):
        return None
