"""Статусы откликов по переписке в Gmail: отклик, этапы, отказ, оффер — по компаниям.

Доступ только на чтение (scope gmail.readonly): письма не отправляются, не помечаются и не удаляются.
Вход — через OAuth-окно Google в браузере (пароль вводит сам владелец почты), токен хранится
локально в state/gmail_token.json. Прочитанные письма кэшируются в state/mail_cache.json, так что
при следующих запусках скачиваются только новые. Не больше 1 запроса в секунду к Gmail API.
"""
from __future__ import annotations

import base64
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr

from . import config, geo, progress
from .models import html_to_text
from .keys import company_key

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
API = "https://gmail.googleapis.com/gmail/v1/users/me"
SETTINGS = geo.S.get("mail", {})
QUERY = SETTINGS.get("query", "newer_than:365d -in:sent -in:chats -in:spam -in:trash")
BODY_CHARS = 4000  # сколько текста письма хранить и разбирать

# --- Чьи это письма ------------------------------------------------------------------------

# ATS и площадки: компания — в имени отправителя или в теме, а не в домене
ATS_DOMAINS_RX = re.compile(
    r"greenhouse(?:-mail)?\.io|lever\.co|ashbyhq\.com|workable(?:mail)?\.com|teamtailor(?:-mail)?\.com|"
    r"recruitee(?:-mailbox)?\.com|bamboohr\.com|breezy\.hr|smartrecruiters\.com|myworkday(?:jobs)?\.com|workday\.com|"
    r"personio\.\w+|pinpointhq\.com|comeet\.\w+|jobvite\.com|icims\.\w+|successfactors\.\w+|eightfold\.ai|"
    r"huntflow\.\w+|hh\.ru|headhunter\.\w+|career\.habr\.com|habr\.com|linkedin\.com|djinni\.co|"
    r"getmatch\.ru|hirify\.\w+|welcometothejungle\.com|join\.com|otta\.com|wellfound\.com|indeed\.com|"
    r"gem\.com|rippling\w*\.com|applytojob\.com|jazzhr\.com|breezy-mail\.com|hackerrank\w*\.com|canditech\.io|"
    r"talentlyft\.com|starred\.com|comeet-notifications\.com|calendly\.com|sage\.hr|testgorilla\.com|"
    r"codility\.com|codesignal\.com|google\.com|zoom\.us|hibob\.com|teamtailor\.com",
    re.I,
)
# поддомен ATS = компания: acme.teamtailor-mail.com, globex.comeet-notifications.com
_ATS_SUBDOMAIN_RX = re.compile(
    r"^(?:[\w-]+\.)?([\w-]+)\.(?:teamtailor-mail|comeet-notifications|bamboohr|breezy(?:-mail)?|recruitee)\.", re.I)
# адрес вида acme-jobs@m.personio.com, globex-careers@… — компания в имени ящика
_LOCAL_COMPANY_RX = re.compile(r"^([a-z][\w]{2,30}?)[-_.](?:jobs|careers?|recruit\w*|hiring|talent|hr)\b", re.I)
# в имени отправителя: "Jane Smith from Acme", "John Doe - Globex", "Recruitment via Sage HR"
_DISPLAY_COMPANY_RX = re.compile(r"(?:\s(?:from|at)\s|\s[-–—|]\s)(?P<c>.+)$", re.I)
_PERSON_RX = re.compile(r"^[A-ZА-ЯЁ][a-zа-яёćčšžđ'-]+\.?\s+[A-ZА-ЯЁ][a-zа-яёćčšžđ'.-]+$")
# «компания», которая на деле должность: "Backend Engineer", "Senior Python Developer"
_ROLE_NAME_RX = re.compile(
    r"\b(?:engineer|developer|software engineer|python|backend|position|role|vacancy|вакансия|разработчик)\b", re.I)
FREE_MAIL_RX = re.compile(
    r"gmail\.com|googlemail\.com|yandex\.\w+|ya\.ru|mail\.ru|bk\.ru|inbox\.ru|list\.ru|outlook\.\w+|"
    r"hotmail\.\w+|live\.\w+|icloud\.com|me\.com|proton(?:mail)?\.\w+|yahoo\.\w+|gmx\.\w+",
    re.I,
)
# "Acme Hiring Team", "Talent Acquisition at Globex", "Greenhouse" -> только название компании
_NAME_NOISE_RX = re.compile(
    r"\b(?:the\s+)?(?:hiring|recruit\w*|talent(?:\s+acquisition)?|careers?|jobs?|people|hr|team|"
    r"no-?reply|notifications?|mailer|via|at|from|группа|команда|подбор\w*|рекрут\w*|отдел|персонал\w*)\b",
    re.I,
)
_GENERIC_SENDER_RX = re.compile(
    r"^(?:greenhouse|lever|ashby|workable|teamtailor|recruitee|bamboohr|breezy|smartrecruiters|workday|"
    r"linkedin|hh\.?ru|headhunter|habr|huntflow|djinni|indeed|getmatch|jobs?|careers?|noreply|no-reply|"
    r"notifications?|team|hr|support|info|interview|application|applications|business opportunity|opportunity|"
    r"update|app|mailbox|icims|bob|hibob|sage|sage hr|"
    r"re|fwd?|canceled|cancelled|clarification|confirmation|invitation|updated invitation|reminder|"
    r"application submitted|application received|thank you|thanks|screen invitation)?$",
    re.I,
)
# ваше имя в подписи / отправителе (settings.toml [mail].own_names) — не название компании
_OWN_NAMES = {n.strip().lower() for n in geo.S.get("mail", {}).get("own_names", []) if n.strip()}
# компания в теме письма: "Thank you for applying to Acme", "Your application was sent to Globex",
# "Interview with Initech", "Отклик на вакансию … в компанию «Ромашка»"
_SUBJECT_COMPANY_RXS = [
    # "... Senior Software Engineer at NVIDIA" — компания после последнего "at" в конце темы
    re.compile(r"\b(?i:at)\s+(?P<c>[A-Z][\w&.'’-]{1,30}(?:\s[A-Z][\w&.'’-]{1,30})?)\s*[!.]?\s*$"),
    # «Your Application at Acme – …», «Thank you for applying to Acme + Globex Labs!»
    re.compile(r"(?i:applying|application|applied|interest|sent)\s+(?:to|at|for|with|in)\s+(?:the\s+)?"
               r"(?P<c>[A-Z0-9][\w&.'’+ -]{1,40}?)"
               r"(?:\s*[!.,:|–—-]|\s+(?:for|has|was|is|team)\b|$)"),
    # LinkedIn: «Jane, your application was sent to Acme» / «… sent to globex.» — название бывает со строчной
    re.compile(r"application was sent to\s+(?P<c>[\w&'’+-][\w&.'’+ -]{0,38}?)\.?\s*$", re.I),
    re.compile(r"(?i:interview|call|chat|meeting|invitation|offer|update|next steps?|introduction|request)\s+"
               r"(?i:with|from|at|to|for)\s+"
               r"(?P<c>[A-Z0-9][\w&.'’ -]{1,40}?)(?=\s*(?:[^\w\s&.'’-]|$)|\s+(?:for|team|on|@)\b)"),
    re.compile(r"^(?P<c>[A-Z][\w&.'’]{1,30}) is on it\b"),  # Greenhouse: «Acme is on it!»
    re.compile(r"(?:applying|interest)\s+(?:to|in)\s+(?P<c>[a-z][\w&.'’-]{2,30})\.?\s*$"),
    re.compile(r"invited by\s+(?P<c>[A-Z0-9][\w&.'’-]{1,30}(?:\s[A-Z][\w&.'’-]{1,30})?)"),
    re.compile(r"^(?P<c>[A-Z][\w&.'’]{1,30}(?:\s[A-Z][\w&.'’]{1,30})?)(?:\s+[-–—|]\s|:\s)"),
    re.compile(r"\s[-–—|]\s(?P<c>[\w&'’][\w&.'’ ]{1,30}?)\.?\s*$"),
    re.compile(r"(?:в компани[июи]|от компании|компания)\s+[«\"]?(?P<c>[^»\"\n,.!]{2,40})", re.I),
]
# в тексте письма площадки (hh.ru, LinkedIn): "Компания «Ромашка» приглашает…", "your application to Globex"
_BODY_COMPANY_RXS = [
    re.compile(r"компани[яиюе]\s+[«\"](?P<c>[^»\"\n]{2,40})[»\"]", re.I),
    re.compile(r"(?:application|applying)\s+(?:to|at|with)\s+(?P<c>[A-Z][\w&.'’-]{1,30}(?:\s[A-Z][\w&.'’-]{1,30})?)"),
    # «…the Software Engineer position with Acme», «interest in the position at Globex!»
    re.compile(r"position (?:at|with)\s+(?P<c>[A-Z][\w&.'’-]{1,30}(?:\s[A-Z][\w&.'’-]{1,30})?)(?=[!.,\s])"),
    # «Thank you for your interest in Initech's Senior Backend Developer role»
    re.compile(r"interest in\s+(?P<c>[A-Z][\w&.-]{1,30}(?:\s[A-Z][\w&.-]{1,30})?)(?:'s|’s)\s"),
]

# --- Что это за письмо ----------------------------------------------------------------------

OFFER, REJECTED, INTERVIEW, APPLIED, OTHER = "offer", "rejected", "interview", "applied", "other"
_OFFER_RX = re.compile(
    r"offer letter|job offer|pleased to (?:extend|offer)|extend (?:you )?an offer|оффер|предложение о работе", re.I)
_REJECT_RX = re.compile(
    r"unfortunately|regret to inform|not (?:to )?(?:be )?mov(?:e|ing) forward|"
    r"decided (?:to )?(?:move|proceed|go|continue) (?:forward )?with (?:other|candidates|applicants|someone|another)|"
    r"other candidates|more closely align|"
    r"not (?:be )?(?:a |the )?(?:right |strong )?(?:fit|match)\b|position has been filled|no longer (?:being )?consider|"
    r"won't be (?:moving|progressing)|not (?:been )?selected|"
    # мягкие формулировки
    r"decided not to (?:proceed|move|progress|continue)|will not be (?:moving|proceeding|progressing)|"
    r"(?:went|go|going) (?:in )?(?:a )?different direction|pursue other (?:candidates|applicants)|"
    r"keep (?:your )?(?:cv|resume|details|profile|information) on file|"
    r"(?:role|position|vacancy) (?:is|has been) (?:now )?(?:put )?(?:on hold|closed|cancelled|canceled)|"
    r"к сожалению|отказ|не готовы (?:пригласить|сделать|предложить|продолжить)|"
    r"приняли решение не|решили продолжить с другим|выбрали другого кандидата|не можем предложить",
    re.I,
)
# «Unfortunately, due to the volume of applications, we can't reply to everyone» — дисклеймер в
# подтверждении отклика, а не отказ: такие предложения при поиске отказа не учитываются
_DISCLAIMER_RX = re.compile(
    r"[^.!?\n]*(?:(?:can(?:no|')t|unable to|not (?:be )?able to)\s+(?:\w+\s+){0,3}?(?:reply|respond|answer|get back|"
    r"contact|provide (?:individual )?feedback)|due to (?:the )?(?:high |large )?(?:volume|number)|"
    r"не (?:сможем|можем) ответить (?:всем|каждому))[^.!?\n]*",
    re.I,
)
# начало процитированной переписки: всё ниже — старые письма, не это
_QUOTE_START_RX = re.compile(
    r"^\s*(?:On .{5,200}? wrote:|.{5,200}? (?:пишет|написал\w*):|-{2,}\s*Original Message\s*-{2,}|"
    r"-{2,}\s*Forwarded message\s*-{2,}|From:\s.+\n(?:Sent|Date):|_{10,})\s*$",
    re.I | re.M,
)
# реальное приглашение: ссылка на созвон, Calendly, выбор слота, «пригласить»
_INVITE_RX = re.compile(
    r"invite you|invitation:|calendly\.com|book (?:a|your) (?:time|slot|call)|"
    r"would (?:like|love) to (?:meet|talk|chat|speak|invite|schedule)|meet\.google\.com|zoom\.us/j|"
    r"teams\.microsoft\.com/l/meetup|пригла[сш]\w+|слот\w*|удобн\w+ (?:время|день)|перенес\w+ (?:нашу )?встреч\w+|"
    r"собеседовани\w+ (?:назначено|состоится)|созвон\w*|"
    r"(?:let us know|share|provide|enter) (?:when you(?:'re| are) available|your availability)|availability request|"
    r"укажи\w* удобн\w+",
    re.I,
)
# этапы процесса: в подтверждении отклика это описание «что дальше», а не приглашение
_STAGE_RX = re.compile(
    r"schedule (?:a|an|your)\b|next (?:step|stage|round)s?\b|technical (?:interview|call|screen|assessment)|"
    r"coding (?:challenge|assessment|interview)|home assignment|take-home|test (?:task|assignment)|"
    r"hackerrank|codility|codesignal|тех(?:ническ\w+)? собеседовани\w+|тестов\w+ задани\w+|следующ\w+ этап",
    re.I,
)
_INTERVIEW_STRONG_RX = re.compile(f"{_INVITE_RX.pattern}|{_STAGE_RX.pattern}", re.I)
_APPLIED_RX = re.compile(
    r"thank(?:s| you) for (?:your )?(?:applying|application|interest)|"
    r"application (?:was |has been )?(?:received|submitted|sent)|we(?:'ve| have) received your application|"
    r"your application (?:to|for|was sent|has been)|application confirmation|confirmation of (?:your )?application|application receipt|"
    r"спасибо за (?:отклик|интерес)|ваш отклик|отклик (?:на вакансию|отправлен|получен)|резюме (?:получено|отправлено)",
    re.I,
)
_INTERVIEW_WEAK_RX = re.compile(r"\binterview|собеседован|интервью", re.I)
_IGNORE_SUBJECT_RX = re.compile(
    r"job alert|jobs? for you|recommended jobs|new jobs|вакансии для вас|подборка вакансий|newsletter|digest|"
    r"verify your (?:email|account)|confirm your email|password|security alert|подтвердите|"
    r"log ?in to|welcome to|security code|verification code|how (?:we did|was your experience)|"
    r"stay up-to-date|survey|опрос|код подтверждения|what made you apply|recovery link",
    re.I,
)


_CONDITIONAL_RX = re.compile(r"[^.!?\n]*\b(?:if|should|once|whether|если|в случае)\b[^.!?\n]*", re.I)


def own_text(body: str) -> str:
    """Текст письма без процитированной переписки и строк, начинающихся с «>»."""
    m = _QUOTE_START_RX.search(body)
    if m:
        body = body[: m.start()]
    return "\n".join(line for line in body.splitlines() if not line.lstrip().startswith(">"))


def classify(subject: str, body: str) -> str:
    """Тип письма: оффер > отказ > этап в теме > отклик в теме > этап в тексте > отклик в тексте.

    Разбирается только собственный текст письма (без цитат). Условные фразы («If your profile fits,
    we will schedule an interview», «if we decide not to move forward…») и дисклеймеры («we can't
    reply to everyone») не считаются ни приглашением, ни отказом.
    """
    body = own_text(body)
    plain = _DISCLAIMER_RX.sub(" ", _CONDITIONAL_RX.sub(" ", body))
    rejected = bool(_REJECT_RX.search(subject) or _REJECT_RX.search(plain))
    # «Confirmation of Application Receipt»: в описании процесса бывает «…to a much-anticipated offer letter» —
    # оффер из текста не засчитываем, если тема — подтверждение отклика
    offer_in_body = _OFFER_RX.search(plain) and not _APPLIED_RX.search(subject)
    if (_OFFER_RX.search(subject) or offer_in_body) and not rejected:
        return OFFER
    if rejected:
        return REJECTED
    if _INTERVIEW_STRONG_RX.search(subject):
        return INTERVIEW
    if _APPLIED_RX.search(subject):
        return APPLIED
    if _INTERVIEW_WEAK_RX.search(subject):
        return INTERVIEW
    if _INVITE_RX.search(plain):
        return INTERVIEW
    if _APPLIED_RX.search(body):
        return APPLIED
    if _STAGE_RX.search(plain):
        return INTERVIEW
    return OTHER


def _clean_name(name: str) -> str:
    name = re.sub(r"[\"“”«»()\[\]]", "", name)
    name = _NAME_NOISE_RX.sub(" ", name)
    return re.sub(r"\s+", " ", name).strip(" -–—|,:.@")


def _display_company(display: str) -> str:
    """Компания из имени отправителя у ATS; имя человека («Jane Smith») — не компания."""
    m = _DISPLAY_COMPANY_RX.search(display)
    if m:
        return m.group("c")
    # «Acme @ icims» — компания до « @ »; «Globex School/Backend Python Developer» — до «/»
    m = re.match(r"^(?P<c>[^@/]{2,40}?)\s*(?:\s@\s|/)", display)
    if m:
        return m.group("c")
    return "" if _PERSON_RX.match(display.strip()) else display


def _domain_company(domain: str) -> str:
    """recruiter@acme.com -> Acme, hr@team.getglobex.com -> Globex."""
    parts = domain.split(".")
    base = parts[-3] if len(parts) >= 3 and parts[-2] in {"co", "com", "org", "net"} else parts[-2] if len(parts) >= 2 else ""
    base = re.sub(r"^(?:get|join|try|go|hello)(?=\w{3})", "", base)
    return base.capitalize()


def company_of(sender: str, subject: str, body: str) -> str:
    """Название компании или '' — если ни тема, ни отправитель, ни текст его не выдают.

    Порядок: тема («Thank you for applying to X», «… at X»), имя отправителя у ATS, поддомен ATS,
    текст письма площадки, корпоративный домен. LinkedIn и календари пишут от имени человека —
    у них имя отправителя не используется.
    """
    aliases = {company_key(k): v for k, v in SETTINGS.get("company_aliases", {}).items()}
    # ручная привязка: имя или адрес -> компания — для писем, где компании нет нигде. Ищется в отправителе
    # и в тексте: HiBob пишет от «No-reply (via Bob)», а имя рекрутера есть только в подписи
    head = body[:3000].lower()
    for part, company in SETTINGS.get("sender_company", {}).items():
        if part.lower() in sender.lower() or part.lower() in head:
            return company
    display, address = parseaddr(sender)
    domain = address.rpartition("@")[2].lower()
    via_service = bool(ATS_DOMAINS_RX.search(domain))
    candidates = [m.group("c") for rx in _SUBJECT_COMPANY_RXS if (m := rx.search(subject))]
    if via_service and not re.search(r"linkedin|calendly|google\.com|zoom\.us", domain):
        candidates.append(_display_company(display))
        sub_m = _ATS_SUBDOMAIN_RX.match(domain)
        if sub_m:
            candidates.append(re.sub(r"-(?:group|inc|ltd|hq)$", "", sub_m.group(1)))
        local_m = _LOCAL_COMPANY_RX.match(address)
        if local_m:
            candidates.append(local_m.group(1))
    # Google Calendar пишет от calendar-notification@google.com, а организатора ставит в имя:
    # «j.smith@acme.com (Google Calendar)» — компания по его корпоративному домену
    organizer = re.search(r"[\w.+-]+@([\w-]+(?:\.[\w-]+)+)", display)
    if organizer and not FREE_MAIL_RX.search(organizer.group(1)) and not ATS_DOMAINS_RX.search(organizer.group(1)):
        candidates.append(_domain_company(organizer.group(1).lower()))
    if via_service or FREE_MAIL_RX.search(domain):
        candidates += [m.group("c") for rx in _BODY_COMPANY_RXS if (m := rx.search(body[:1500]))]
    else:
        candidates.append(_domain_company(domain))
    for name in candidates:
        name = re.sub(r"^the\s+", "", _clean_name(name), flags=re.I)
        if name.isupper() and len(name) > 4:  # «JOB OFFER AT ACME CORP»
            name = name.title()
        if (name and len(name) <= 40 and not _GENERIC_SENDER_RX.match(name) and not _ROLE_NAME_RX.search(name)
                and name.lower() not in _OWN_NAMES):
            return aliases.get(company_key(name), name)
    return ""


# --- Gmail API ------------------------------------------------------------------------------

LOGIN_TIMEOUT_SECONDS = 180  # сколько ждать вход в браузере
SYNC_STALE_HOURS = 24  # почта старше — в отчётах появляется предупреждение


def cached_messages() -> list[dict]:
    """Письма из локального кэша — когда Gmail недоступен."""
    if not config.MAIL_CACHE_FILE.exists():
        return []
    return list(json.loads(config.MAIL_CACHE_FILE.read_text(encoding="utf-8")).values())


def _read_sync() -> dict:
    if not config.MAIL_SYNC_FILE.exists():
        return {}
    return json.loads(config.MAIL_SYNC_FILE.read_text(encoding="utf-8"))


def save_sync(error: str = "") -> None:
    """Запоминает итог обновления почты: при успехе — время, при ошибке — её текст."""
    state = _read_sync()
    if not error:
        state["ok_at"] = datetime.now(timezone.utc).isoformat()
    state["error"] = error
    config.MAIL_SYNC_FILE.parent.mkdir(exist_ok=True)
    config.MAIL_SYNC_FILE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def stale_warning() -> str:
    """Текст предупреждения, если статусы откликов устарели; пустая строка — почта свежая."""
    state = _read_sync()
    ok_at = datetime.fromisoformat(state["ok_at"]) if state.get("ok_at") else None
    since = f"данные на {ok_at.astimezone():%d.%m %H:%M}" if ok_at else "свежих данных нет"
    if state.get("error"):
        return f"Почта не обновилась, {since}: {state['error']}. Запустите applications.py"
    if not ok_at:
        return "Почта ещё не обновлялась: запустите applications.py"
    if (datetime.now(timezone.utc) - ok_at).total_seconds() > SYNC_STALE_HOURS * 3600:
        return f"Почта давно не обновлялась, {since}. Запустите applications.py"
    return ""

def credentials():
    """OAuth-токен Gmail (только чтение). Окно входа Google открывается при первом запуске и когда токен отозван."""
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    creds = None
    if config.GMAIL_TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(config.GMAIL_TOKEN_FILE), SCOPES)
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError as e:
            log.warning("токен Gmail истёк или отозван (%s) — нужен новый вход", e)
            creds = None
    if not creds or not creds.valid:
        if not config.GMAIL_CLIENT_FILE.exists():
            raise FileNotFoundError(
                f"нет {config.GMAIL_CLIENT_FILE.name}: создайте OAuth-клиент «Desktop app» в Google Cloud "
                "(см. docs/applications.md, раздел «One-time Gmail setup»)"
            )
        from google_auth_oauthlib.flow import InstalledAppFlow

        flow = InstalledAppFlow.from_client_secrets_file(str(config.GMAIL_CLIENT_FILE), SCOPES)
        # без таймаута запуск из Планировщика вечно ждал бы вход и не дошёл бы до поиска вакансий
        creds = flow.run_local_server(port=0, open_browser=True, timeout_seconds=LOGIN_TIMEOUT_SECONDS)
    config.GMAIL_TOKEN_FILE.parent.mkdir(exist_ok=True)
    config.GMAIL_TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
    return creds


class Gmail:
    """Чтение писем через Gmail REST API, не чаще 1 запроса в секунду."""

    def __init__(self, creds):
        from google.auth.transport.requests import AuthorizedSession

        self.session = AuthorizedSession(creds)
        self._next = 0.0
        self.requests = 0

    def _get(self, path: str, params: dict | None = None) -> dict:
        delay = self._next - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        self._next = time.monotonic() + config.MIN_INTERVAL
        self.requests += 1
        r = self.session.get(f"{API}/{path}", params=params, timeout=40)
        r.raise_for_status()
        return r.json()

    def address(self) -> str:
        return self._get("profile").get("emailAddress", "")

    def message_ids(self, query: str) -> list[str]:
        ids, token = [], None
        while True:
            body = self._get("messages", {"q": query, "maxResults": 500, **({"pageToken": token} if token else {})})
            ids += [m["id"] for m in body.get("messages", [])]
            token = body.get("nextPageToken")
            if not token:
                return ids

    def message(self, msg_id: str) -> dict:
        raw = self._get(f"messages/{msg_id}", {"format": "full"})
        headers = {h["name"].lower(): h["value"] for h in raw.get("payload", {}).get("headers", [])}
        return {
            "id": raw["id"], "thread": raw.get("threadId", ""),
            "date": int(raw.get("internalDate", 0)) // 1000,
            "from": headers.get("from", ""), "subject": headers.get("subject", ""),
            "body": _body_text(raw.get("payload", {}))[:BODY_CHARS],
        }


def _body_text(payload: dict) -> str:
    """Текст письма: text/plain, а если его нет — text/html без разметки."""
    plain, html = [], []

    def walk(part):
        data = part.get("body", {}).get("data")
        if data:
            text = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")
            (plain if part.get("mimeType") == "text/plain" else html if part.get("mimeType") == "text/html" else []).append(text)
        for sub in part.get("parts", []) or []:
            walk(sub)

    walk(payload)
    return "\n".join(plain) if plain else html_to_text("\n".join(html))


def fetch_messages() -> tuple[list[dict], str]:
    """Все письма по запросу из settings.toml [mail].query (новые — из API, старые — из кэша)."""
    cache = {}
    if config.MAIL_CACHE_FILE.exists():
        cache = json.loads(config.MAIL_CACHE_FILE.read_text(encoding="utf-8"))
    gmail = Gmail(credentials())
    address = gmail.address()
    ids = gmail.message_ids(QUERY)
    new = [i for i in ids if i not in cache]
    log.info("почта %s: писем по запросу %d, новых %d (≈%d с при 1 запросе/с)", address, len(ids), len(new), len(new))
    progress.set_total(len(new))
    for n, msg_id in enumerate(new, 1):
        if progress.skip_requested():  # кнопка «Пропустить»: остальные письма — в следующий раз
            break
        progress.step(f"письмо {n} из {len(new)}")
        try:
            cache[msg_id] = gmail.message(msg_id)
        except Exception as e:  # одно битое письмо не должно ронять весь разбор
            log.warning("письмо %s: %s", msg_id, e)
        if n % 100 == 0:
            log.info("почта: прочитано %d из %d", n, len(new))
            config.MAIL_CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    config.MAIL_CACHE_FILE.parent.mkdir(exist_ok=True)
    config.MAIL_CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    return [cache[i] for i in ids if i in cache], address


# --- Сводка по компаниям --------------------------------------------------------------------

@dataclass
class Event:
    when: datetime
    kind: str
    subject: str
    sender: str
    thread: str


@dataclass
class Application:
    company: str
    events: list = field(default_factory=list)

    @property
    def ordered(self) -> list:
        return sorted(self.events, key=lambda e: e.when)

    @property
    def status(self) -> str:
        """Последнее значимое событие: письма «просто переписка» статус не меняют."""
        meaningful = [e for e in self.ordered if e.kind != OTHER]
        return meaningful[-1].kind if meaningful else OTHER

    @property
    def stages(self) -> int:
        """Сколько разных дней приходили приглашения на этапы."""
        return len({e.when.date() for e in self.events if e.kind == INTERVIEW})

    @property
    def first(self) -> datetime:
        return self.ordered[0].when

    @property
    def last(self) -> Event:
        return self.ordered[-1]


def summarize(messages: list[dict]) -> tuple[list[Application], list[dict]]:
    """-> (компании с событиями, письма без компании, которые похожи на отклик/этап/отказ)."""
    apps: dict[str, Application] = {}
    unknown = []
    ignore_senders = [s.lower() for s in SETTINGS.get("ignore_senders", [])]
    for msg in messages:
        sender, subject, body = msg["from"], msg["subject"], msg["body"]
        if any(s in sender.lower() for s in ignore_senders) or _IGNORE_SUBJECT_RX.search(subject):
            continue
        kind = classify(subject, body)
        if kind == OFFER and re.search(r"inmail|hit-reply@linkedin", sender, re.I):
            kind = APPLIED  # «JOB OFFER AT X» в LinkedIn InMail — это рекрутер пишет первым, а не оффер
        company = company_of(sender, subject, body)
        when = datetime.fromtimestamp(msg["date"], tz=timezone.utc)
        if not company:
            if kind != OTHER:
                unknown.append({**msg, "kind": kind})
            continue
        if kind == OTHER and ATS_DOMAINS_RX.search(sender) is None and FREE_MAIL_RX.search(sender) is None:
            # обычное письмо с корпоративного домена без признаков отклика — скорее всего, не про найм
            if not re.search(r"recruit|talent|hiring|career|hr\b|people|подбор|рекрут", sender, re.I):
                continue
        _add_event(apps, company, Event(when, kind, subject, sender, msg["thread"]))
    for company, event in manual_events():
        _add_event(apps, company, event)
    return sorted(apps.values(), key=lambda a: a.last.when, reverse=True), unknown


def _add_event(apps: dict, company: str, event: Event) -> None:
    key = company_key(company) or company.lower()
    # «Acme» и «Acme Industries, LLC», «Globex AI» и «Globex» — одна компания
    key = next((k for k in apps if min(len(k), len(key)) >= 4 and (k.startswith(key) or key.startswith(k))), key)
    app = apps.setdefault(key, Application(company))
    if len(company) < len(app.company):
        app.company = company  # короткое написание обычно чище: «Acme», а не «Acme Industries, LLC»
    app.events.append(event)


# часовой пояс дат без времени в applications_manual.toml: settings.toml [mail].utc_offset_hours;
# для статусов точность до часа не важна
_LOCAL_TZ = timezone(timedelta(hours=float(geo.S.get("mail", {}).get("utc_offset_hours", 0))))


def manual_events() -> list[tuple[str, Event]]:
    """События из applications_manual.toml (Telegram, LinkedIn, звонки) — у них нет ссылки на письмо."""
    if not config.MANUAL_APPLICATIONS_FILE.exists():
        return []
    import tomllib

    data = tomllib.loads(config.MANUAL_APPLICATIONS_FILE.read_text(encoding="utf-8"))
    result = []
    for entry in data.get("application", []):
        for e in entry.get("events", []):
            when = e["date"]
            if not isinstance(when, datetime):  # дата без времени
                when = datetime(when.year, when.month, when.day, 12)
            if when.tzinfo is None:
                when = when.replace(tzinfo=_LOCAL_TZ)
            kind = e.get("kind", OTHER)
            if kind not in (OFFER, REJECTED, INTERVIEW, APPLIED, OTHER):
                log.warning("%s: неизвестный kind %r в %s", entry["company"], kind, config.MANUAL_APPLICATIONS_FILE.name)
                kind = OTHER
            result.append((entry["company"], Event(when, kind, e.get("note", ""), entry.get("source", "вручную"), "")))
    return result
