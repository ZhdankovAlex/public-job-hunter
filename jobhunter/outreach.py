"""Контакт с живым человеком по вакансии: прямые контакты из текста, поиск людей в LinkedIn, черновики.

Скрипт ничего не отправляет и в LinkedIn не ходит: он только находит контакты в тексте вакансии,
собирает ссылки на поиск людей (их открывает пользователь сам) и черновики коротких сообщений —
рекрутеру и инженеру компании с просьбой о реферале, — которые пользователь копирует и отправляет вручную.
"""
from __future__ import annotations

import re
from urllib.parse import quote

# лимит заметки к запросу на контакт в LinkedIn — 300 символов
NOTE_LIMIT = 300


def _outreach_settings() -> tuple[str, str]:
    """(кто вы, где вы) для черновиков: settings.toml [outreach], по умолчанию — из [home]."""
    from . import config, geo  # geo читает settings.toml при загрузке

    cfg = geo.S.get("outreach", {})
    pitch = cfg.get("pitch") or "Backend Engineer with Python and PostgreSQL experience"
    based = cfg.get("based_in") or (f"Based in {config.HOME_COUNTRY}." if config.HOME_COUNTRY else "")
    return pitch, based

# навыки, которые и так есть в первой фразе сообщения
_BASE_SKILLS = {"python", "postgresql", "sql"}

_PEOPLE_SEARCH = "https://www.linkedin.com/search/results/people/?keywords="


def real_company(company: str) -> str:
    """Название компании или пустая строка для заглушек «—» и «Fintech (компания не названа)»."""
    company = (company or "").strip()
    return "" if company in ("", "—") or "не назван" in company else company


def search_links(company: str) -> list[tuple[str, str]]:
    """Ссылки на поиск людей компании в LinkedIn: [(подпись, url)]; без компании — пусто.

    Args:
        company: название компании, как в вакансии.

    Returns:
        Поиск рекрутеров и поиск руководителей разработки.
    """
    company = real_company(company)
    if not company:
        return []
    return [
        ("рекрутер", _PEOPLE_SEARCH + quote(f"{company} recruiter")),
        ("руководитель", _PEOPLE_SEARCH + quote(f"{company} engineering manager")),
        ("инженер для реферала", _PEOPLE_SEARCH + quote(f"{company} backend engineer")),
    ]


_EMAIL_RX = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[a-z]{2,}(?![\w-])", re.I)
_TG_LINK_RX = re.compile(r"(?:https?://)?t\.me/([A-Za-z][\w]{3,})(?![\w/])")
_TG_HANDLE_RX = re.compile(r"(?<![\w@./])@([A-Za-z][A-Za-z0-9_]{3,})\b(?!\.\w)")  # не «@company.com»
_LINKEDIN_RX = re.compile(r"(?:https?://)?(?:[a-z]{2,3}\.)?linkedin\.com/in/[\w%-]+/?", re.I)
# адреса, по которым не пишут о вакансии: рассылки, юристы, поддержка, приватность
_SKIP_EMAIL_RX = re.compile(
    r"^(?:no-?reply|do-?not-?reply|privacy|gdpr|dpo|legal|support|help|abuse|security|billing|accounts?|"
    r"unsubscribe|notifications?)@|accommodat|accessib|privacy|compliance|\beeo\b|ethics|whistle", re.I)
_TELEGRAM_WORD_RX = re.compile(r"telegram|телеграм|\btg\b|тг\b", re.I)
# @упоминания, которые не контакты: служебные имена, домены, наши каналы-источники
_SKIP_HANDLE_RX = re.compile(r"^(?:gmail|outlook|yahoo|mail|example|channel|admin)$", re.I)


def contacts(text: str, channel: str = "") -> list[tuple[str, str, str]]:
    """Прямые контакты из текста вакансии: [(вид, что показать, ссылка)] без повторов.

    Виды: email, telegram, linkedin. Не считаются контактами служебные адреса (noreply, privacy,
    support…) и упоминание самого канала-источника.

    Args:
        text: текст вакансии или поста.
        channel: Telegram-канал, откуда пост (его @упоминание — не контакт).
    """
    found: dict[str, tuple[str, str, str]] = {}
    for email in _EMAIL_RX.findall(text or ""):
        if not _SKIP_EMAIL_RX.search(email):
            found.setdefault(email.lower(), ("email", email, f"mailto:{email}"))
    emails_text = " ".join(found)
    # @handle — Telegram только в посте из Telegram или рядом со словом «Telegram»: в текстах с ATS
    # собачка обычно означает Instagram / X или домен почты
    handles = list(_TG_LINK_RX.findall(text or ""))
    for m in _TG_HANDLE_RX.finditer(text or ""):
        near = (text or "")[max(0, m.start() - 60): m.end() + 60]
        if channel or _TELEGRAM_WORD_RX.search(near):
            handles.append(m.group(1))
    for handle in handles:
        low = handle.lower()
        if low == channel.lower() or _SKIP_HANDLE_RX.match(handle) or f"@{low}." in emails_text:
            continue
        found.setdefault(f"tg:{low}", ("telegram", f"@{handle}", f"https://t.me/{handle}"))
    for url in _LINKEDIN_RX.findall(text or ""):
        href = url if url.startswith("http") else f"https://{url}"
        found.setdefault(href.lower().rstrip("/"), ("linkedin", url.split("/in/")[-1].strip("/"), href))
    return list(found.values())


def telegram_contact(job) -> str:
    """Ссылка на автора вакансии в Telegram, если пост дал @handle; иначе пустая строка."""
    handle = getattr(job, "contact", "").lstrip("@")
    return f"https://t.me/{handle}" if handle else ""


def _hook(job) -> str:
    """Две-три совпавшие ключевые технологии вакансии сверх Python и PostgreSQL.

    Первыми идут навыки, по которым вакансия совпала с профилем (matched_skills), потом остальные.
    """
    have = [s for s in getattr(job, "cv_core_have", []) if s.lower() not in _BASE_SKILLS]
    matched = {s.lower() for s in getattr(job, "matched_skills", [])}
    skills = sorted(have, key=lambda s: s.lower() not in matched)[:3]
    if not skills:
        return ""
    listed = skills[0] if len(skills) == 1 else ", ".join(skills[:-1]) + f" and {skills[-1]}"
    return f", hands-on with {listed}"


def draft(job) -> str:
    """Черновик сообщения на английском, не длиннее NOTE_LIMIT символов.

    Если полный вариант не помещается, сначала убирается перечень технологий,
    потом название вакансии заменяется общим «a backend engineer».

    Args:
        job: вакансия (Job или Telegram-вакансия) с company, title и cv_core_have.

    Returns:
        Текст для ручной отправки.
    """
    company = real_company(job.company) or "your company"
    title = " ".join((job.title or "").split())
    pitch, based = _outreach_settings()
    tail = " ".join(filter(None, [based, "Would you be the right person to talk to about it? Thanks!"]))
    role = f"the {title} position" if title else "a backend engineer"
    variants = [(role, _hook(job)), (role, ""), ("a backend engineer", "")]
    for role, hook in variants:
        text = (f"Hi! I noticed {company} is hiring for {role}. "
                f"I'm a {pitch}{hook}. {tail}")
        if len(text) <= NOTE_LIMIT:
            return text
    return text[:NOTE_LIMIT - 1].rstrip() + "…"


def referral_draft(job) -> str:
    """Черновик просьбы о реферале инженеру компании (не рекрутеру), не длиннее NOTE_LIMIT.

    Коротко, с готовым «пакетом»: кто вы, на какую роль, что пришлёте — чтобы ответить было легко.
    """
    company = real_company(job.company) or "your company"
    title = " ".join((job.title or "").split())
    pitch, based = _outreach_settings()
    tail = " ".join(filter(None, [based, "Would you be open to referring me? "
                                         "I'll send my CV and a 2-line summary to make it easy. Thanks!"]))
    variants = [(f"the {title} role" if title else "a backend role", _hook(job)),
                (f"the {title} role" if title else "a backend role", ""), ("a backend role", "")]
    for role, hook in variants:
        text = (f"Hi! I'm applying for {role} at {company}. "
                f"{pitch}{hook}. {tail}")
        if len(text) <= NOTE_LIMIT:
            return text
    return text[:NOTE_LIMIT - 1].rstrip() + "…"
