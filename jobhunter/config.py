"""Все настройки в одном месте: пути, таблица, фильтры."""
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def local_or_example(path: Path) -> Path:
    """Личный файл (его нет в репозитории) или, если его ещё не создали, пример рядом: settings.example.toml."""
    if path.exists():
        return path
    example = path.with_name(f"{path.stem}.example{path.suffix}")
    return example if example.exists() else path

COMPANIES_FILE = local_or_example(ROOT / "companies.txt")  # нет своего — companies.example.txt
MAPPING_FILE = ROOT / "ats_mapping.json"  # кэш найденных ATS-бордов, создаётся сам
SEEN_FILE = ROOT / "state" / "seen.json"
REPORTS_DIR = ROOT / "reports"
ERRORS_LOG = ROOT / "errors.log"  # предупреждения и ошибки текущего прогона
ERRORS_LOG_PREV = ROOT / "errors.prev.log"  # прошлого прогона (перезаписывается при каждом новом)
STAFFING_FILE = local_or_example(ROOT / "staffing_blacklist.txt")
SETTINGS_FILE = local_or_example(ROOT / "settings.toml")  # нет своего — settings.example.toml
PROFILE_FILE = local_or_example(ROOT / "profile.toml")  # нет своего — profile.example.toml
# Почта с перепиской с рекрутерами (applications.py): OAuth-клиент Google и локальные токен/кэш
GMAIL_CLIENT_FILE = ROOT / "gmail_client.json"
GMAIL_TOKEN_FILE = ROOT / "state" / "gmail_token.json"
MAIL_CACHE_FILE = ROOT / "state" / "mail_cache.json"
JOBS_ARCHIVE_DIR = ROOT / "state" / "jobs"  # вакансии каждого отчёта с текстом: <дата>.json
MAIL_SYNC_FILE = ROOT / "state" / "mail_sync.json"  # когда почта обновилась и чем закончилась последняя попытка
# отклики не из почты (Telegram, LinkedIn): дописываются руками, объединяются с письмами
MANUAL_APPLICATIONS_FILE = ROOT / "applications_manual.toml"

# База резюме для генератора (телефон, почта — личное): нет своей — cv/base.example.toml
CV_BASE_FILE = local_or_example(ROOT / "cv" / "base.toml")

_SETTINGS = tomllib.loads(SETTINGS_FILE.read_text(encoding="utf-8"))

# --- Домашняя страна: settings.toml [home] -----------------------------------
# Вакансии в ней (офис, гибрид, remote) показываются наравне с remote по миру. Пусто — только remote.
_HOME = _SETTINGS.get("home", {})
HOME_COUNTRY = str(_HOME.get("country", "")).strip()
HOME_ISO = str(_HOME.get("iso", "")).strip().upper()  # ISO-код страны для запросов к Himalayas / freehire
_HOME_NAMES = [n for n in [HOME_COUNTRY, *_HOME.get("places", [])] if str(n).strip()]
_NEVER = r"(?!x)x"  # регулярка, которая ничего не находит (домашняя страна не задана)

USER_AGENT = "job-hunter/1.0 (open-source job search script; 1 req/s)"
MIN_INTERVAL = 1.0  # секунд между запросами к одному хосту
# Workable отвечает 429 уже при 1 req/s — для него пауза больше
HOST_INTERVALS = {"apply.workable.com": 6.0, "t.me": 2.0}

# Публичные Telegram-каналы (веб-превью t.me/s/...): settings.toml [telegram], по умолчанию выключено
_TELEGRAM = _SETTINGS.get("telegram", {})
TELEGRAM_ENABLED = bool(_TELEGRAM.get("enabled", False))
TELEGRAM_CHANNELS = list(_TELEGRAM.get("channels", []))
TELEGRAM_MAX_AGE_DAYS = int(_TELEGRAM.get("max_age_days", 60))
# Каналы русскоязычного рынка: их вилки — отдельная группа в базе зарплат (salary.py)
TELEGRAM_CIS_CHANNELS = set(_TELEGRAM.get("cis_channels", []))
TELEGRAM_MAX_PAGES = 25  # страховка от бесконечной листалки
WORKERS = 8 # потоки упираются в лимит по хосту, а не друг в друга

# Через сколько дней заново искать slug для компаний, не найденных ни на одной ATS
RECHECK_NOT_FOUND_DAYS = 30

ARBEITNOW_PAGES = 5

# Himalayas: robots.txt запрещает &page=, поэтому вместо пагинации —
# несколько узких запросов, по одной (свежей) странице каждый.
HIMALAYAS_QUERIES = [
    {"q": "python backend", "worldwide": "true"},
    {"q": "python", "worldwide": "true"},
    {"q": "django", "worldwide": "true"},
    {"q": "fastapi", "worldwide": "true"},
    {"q": "backend engineer", "worldwide": "true"},
]
if HOME_ISO:  # плюс вакансии в домашней стране
    HIMALAYAS_QUERIES += [{"q": "python", "country": HOME_ISO}, {"q": "backend", "country": HOME_ISO}]

I = re.IGNORECASE

# --- Ключевые слова -------------------------------------------------------
PYTHON_RX = re.compile(r"\b(?:python|django|flask|fastapi)\b", I)
KEYWORD_RX = re.compile(
    r"\b(?:python|django|flask|fastapi|postgres(?:ql)?|back[\s-]?end)\b", I
)

# Заголовок должен быть похож на инженерную роль (иначе "backend" в описании
# вакансии сейлза тоже проходит фильтр).
TITLE_ROLE_RX = re.compile(
    r"engineer|developer|programmer|back[\s-]?end|python|django|architect|"
    r"tech(?:nical)? lead|\bswe\b|\bsde\b|разработчик|программист",
    I,
)
TITLE_EXCLUDE_RX = re.compile(
    r"front[\s-]?end|\bios\b|android|mobile|react native|\bintern(?:ship)?\b|"
    r"\bjunior\b|graduate|\b(?:sales|solutions?|support|customer|field|"
    r"forward deployed|hardware|mechanical|electrical) engineer|"
    r"\bqa\b|\bsdet\b|test engineer|designer|recruiter",
    I,
)

# --- Локация ---------------------------------------------------------------
HOME_RX = re.compile(
    "|".join(rf"(?<!\w){re.escape(n)}(?!\w)" for n in _HOME_NAMES) or _NEVER, I
)
REMOTE_RX = re.compile(r"remote|work from home|\bwfh\b|anywhere|world[\s-]?wide|distributed|удал[её]н", I)
BROAD_RX = re.compile(
    r"world[\s-]?wide|global|anywhere|\bemea\b|europe|\beu\b|\bcis\b|international|"
    r"европ|по всему миру|весь мир|где угодно|\bснг\b|"
    r"\bcet\b|\beet\b|(?:utc|gmt)\s?[+±-]?\s?[0-3]\b",
    I,
)
# Регионы, которые точно не подходят (если нет BROAD-маркера рядом)
NON_EU_RX = re.compile(
    r"\b(?:usa?|u\.s\.a?\.?|united states|canada|north america|americas?|"
    r"latam|latin america|apac|asia|india|australia|new zealand|brazil|mexico|"
    r"argentina|colombia|chile|peru|japan|singapore|china|hong kong|korea|"
    r"philippines|vietnam|thailand|indonesia|malaysia|pakistan|israel|uae|"
    r"dubai|saudi|africa|nigeria|kenya|egypt|ghana|"
    r"new york|nyc|san francisco|sf|seattle|austin|boston|chicago|denver|"
    r"los angeles|toronto|vancouver|bangalore|bengaluru|sydney|tokyo)\b",
    I,
)
LOCATION_NOISE_RX = re.compile(
    r"remote(?:ly)?|fully|100%|work from home|\bwfh\b|home office|only|based|"
    r"hybrid|\bor\b|[\s,;:()/\-–—|·•]",
    I,
)

# --- Язык -----------------------------------------------------------------
# "Основные" языки, которые конфликтуют с Python-профилем.
OTHER_LANGS = {
    "Java": re.compile(r"\bjava\b(?!\s*script)", I),
    "Kotlin": re.compile(r"\bkotlin\b", I),
    "Go": re.compile(
        r"(?i:\bgolang\b)|\bGo\b(?=\s*(?:,|/|\)|and\b|or\b|developer|engineer|"
        r"services?|experience|programming|language|backend))|"
        r"(?<=in )Go\b|(?<=[,/] )Go\b"
    ),
    "Ruby": re.compile(r"\bruby\b|\brails\b", I),
    "Scala": re.compile(r"\bscala\b", I),
    "PHP": re.compile(r"\bphp\b|\blaravel\b", I),
    "C#/.NET": re.compile(r"\bc#|\.net\b", I),
}
NICE_TO_HAVE_RX = re.compile(
    r"nice[\s-]to[\s-]have|bonus|\ba plus\b|is a plus|preferred|desirable|"
    r"advantage|optional|familiarity with|будет плюсом|желательно",
    I,
)
REQUIRED_RX = re.compile(
    r"requirements|qualifications|must[\s-]have|what you(?:'ll| will)? (?:need|bring)|"
    r"you have|требования",
    I,
)

# --- Telegram --------------------------------------------------------------
TG_KEYWORD_RX = re.compile(
    r"python|back[\s-]?end|б[эе]к[\s-]?енд|fastapi|django|postgres", I
)
# только IT-роли
TG_ROLE_RX = re.compile(
    r"engineer|developer|programmer|back[\s-]?end|б[эе]к[\s-]?енд|python|django|architect|"
    r"devops|\bsre\b|tech(?:nical)? lead|team ?lead|тимлид|разработчик|программист|инженер|архитектор",
    I,
)
TG_EXCLUDE_RX = re.compile(
    TITLE_EXCLUDE_RX.pattern + r"|\btrainee\b|стаж[её]р|джун|младш|начинающ|автотест|аналитик|фронт[\s-]?енд|тестировщик|c\+\+|\b1[сc]\b|администратор|системный программист",
    I,
)

# --- Язык в НАЗВАНИИ роли (ATS и Telegram) ----------------------------------
TITLE_LANGS = {
    "Go": re.compile(r"(?i:\bgolang\b)|\bGo\b(?!-to)"),
    "Java": re.compile(r"\bjava\b(?!\s*script)", I),
    "Kotlin": re.compile(r"\bkotlin\b", I),
    "Ruby": re.compile(r"\bruby\b|\brails\b", I),
    ".NET": re.compile(r"(?<![a-z0-9])\.net\b|\bdotnet\b|\bc#", I),
    "PHP": re.compile(r"\bphp\b|\blaravel\b", I),
    "Scala": re.compile(r"\bscala\b", I),
    "Node.js": re.compile(r"\bnode(?:\.?js)?\b", I),
    "Rust": re.compile(r"\brust\b", I),
    "C++": re.compile(r"\bc\+\+", I),
}

# --- Staffing / Top ---------------------------------------------------------
CONTRACT_TITLE_RX = re.compile(r"\bcontract(?:or)?\b", I)
TOP_LIMIT = 15
TOP_SENIORITY_EXCLUDE_RX = re.compile(r"\b(?:head|director|vp|vice president|principal)\b", I)
PRIORITY_MAX_ROWS = 60  # строк в разделе ✅, остальное — в Appendix

# Раздел 🌍: роль должна быть инженерной и про Python
RELOC_EXCLUDE_TITLE_RX = re.compile(
    r"value engineer|solutions? (?:architect|engineer)|deployed (?:engineer|architect)|deployment|"
    r"(?:delivery|engineering|product|program|project) manager|\baccount\b|\bsales\b|customer|"
    r"recruit\w*|designer|consult\w*",
    I,
)
RELOC_NEEDS_PYTHON_TITLE_RX = re.compile(
    r"back[\s-]?end|software|developer|platform|\bdata\b|\bml\b|machine learning|\bai\b|engineer", I
)
TOP_PER_COMPANY = 2  # чтобы топ не состоял из одной компании
TOP_TITLE_RX = re.compile(r"(?i:python|back[\s-]?end)|\bAI\b")
TOP_LOCATION_RX = re.compile(
    r"world[\s-]?wide|global|anywhere|\bemea\b|" + HOME_RX.pattern, I
)

# --- Фильтр роли для ВСЕХ разделов -----------------------------------------
ROLE_EXCLUDE_RX = re.compile(
    r"\bhpc\b|network|solutions? architect|"
    r"advocate|recruit\w*|\bsales\b|support|manager|director|\bhead of\b|designer|\bqa\b|"
    r"quality assurance|тестировщик|менеджер|руководител",
    I,
)
# Backend / Software Engineer / Python — нужен любой ключ в тексте
ROLE_CORE_RX = re.compile(
    r"back[\s-]?end|б[эе]к[\s-]?енд|software (?:engineer|developer)|python|django|fastapi|"
    r"разработчик|программист",
    I,
)
# AI/ML Engineer — только если в тексте есть python
ROLE_AIML_RX = re.compile(
    r"\b(?:ai|ml|llm|genai|machine learning)\b.*\b(?:engineer|developer)\b|"
    r"\b(?:engineer|developer)\b.*\b(?:ai|ml|llm|machine learning)\b",
    I,
)

# --- США: коды штатов, U.S., NAMER/AMER — это привязка к стране ----------------
_US_STATES = (
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ "
    "NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC"
).split()
US_BOUND_RX = re.compile(
    r"(?:[-–—,(/]\s*|\bRemote\s+)(?:" + "|".join(_US_STATES) + r")\b|"
    r"\bU\.S\.?(?![a-z])|\bNAMER\b|\bAMER\b|(?i:\bnorth america\b|\bamericas\b)"
)
# Маркер "без привязки" в НАЗВАНИИ роли — только явный регион
# ("International"/"Global" в названии — это имя команды, а не локация)
TITLE_BROAD_RX = re.compile(r"world[\s-]?wide|anywhere|\bemea\b|europe|\beu\b", I)
LOCATION_EUROPE_RX = re.compile(r"world[\s-]?wide|global|\bemea\b|europe|\beu\b", I)

# Статус отклика (applications_manual.toml), с которым компания не показывается в «Новом» и в топе:
# компания в активном процессе (собеседование)
APPLIED_BLOCK_RX = re.compile(r"собес|интервью|interview", I)
# статусы, после которых на вакансию можно откликнуться снова: она показывается как обычно
RETRY_STATUS_RX = re.compile(r"отказ|игнор", I)

# Platform Engineer — допустимая смежная роль, но только с python
ROLE_PLATFORM_RX = re.compile(
    r"\bplatform\b.*\b(?:engineer|developer)\b|\b(?:engineer|developer)\b.*\bplatform\b", I
)

# --- Тип занятости: только full-time (штат или контракт) ----------------------
NOT_FULL_TIME_RX = re.compile(
    r"part[\s-]?time|intern(?:ship)?\b|freelanc\w*|temporary|\btemp\b|hourly|volunteer|per[\s-]diem|"
    r"seasonal|trainee|werkstudent|working student|mini[\s-]?job|"
    r"частичн\w+ занятост\w+|неполн\w+ (?:день|занятост\w+)|фриланс\w*|подработк\w+|стаж[её]р\w*",
    I,
)
# "AI Trainer" — разметка/обучение моделей по часам, не разработка
NOT_A_JOB_TITLE_RX = re.compile(r"\bai trainer\b|\bannotator\b|data label", I)
HOURLY_PAY_RX = re.compile(r"[$€£]\s?\d+(?:[.,]\d+)?(?:\s?[-–]\s?[$€£]?\d+(?:[.,]\d+)?)?\s?(?:/|per|an)\s?(?:hour|hr)\b", I)

# --- Скрытие по тексту вакансии ---------------------------------------------
# Разметка/обучение моделей со сдельной оплатой — не разработка
NOT_A_JOB_TEXT_RX = re.compile(r"\bai trainer\b|train AI systems|per[\s-]task\b", I)
# Жёсткая привязка к месту / отказ в релокации; не скрываем, если рядом домашняя страна или EU
RESIDENCY_RX = re.compile(r"must (?:reside|be located|be based|live|be resident) in", I)
# Нет релокации / визовой поддержки — скрываем, только если вакансия привязана к чужой стране
NO_SUPPORT_RX = re.compile(
    r"not (?:providing|offering|able to (?:provide|offer)|provide|offer) (?:any )?(?:relocation|visa)|"
    r"no relocation|relocation (?:support |assistance |package )?(?:is |will )?not (?:be )?(?:provided|offered|available)|"
    r"no visa sponsorship|(?:unable|not able) to sponsor|cannot sponsor|can't sponsor|"
    r"do(?:es)? not (?:offer |provide )?(?:visa )?sponsor\w*|without (?:visa )?sponsorship",
    I,
)
RESIDENCY_OK_RX = re.compile(HOME_RX.pattern + r"|\bEU\b|europe|\bemea\b|european union", I)

# --- Штрафы к баллу релевантности -------------------------------------------
GO_REQUIRED_RX = re.compile(
    r"(?:experience|proficien\w+|expert\w*|strong|fluen\w+|years|skills?|knowledge|written)"
    r"[^.\n]{0,40}?(?:\b[Gg]olang\b|\bGo\b(?!-to|\s+(?:to|live|beyond|above)))"
)
JVM_SPRING_RX = re.compile(
    r"\bJVM\b|Java Virtual Machine|Spring Boot|Spring Framework|\bSpring\b(?=\s*(?:,|/|\)|MVC|Cloud|Data|Security))"
)
YEARS_8_PLUS_RX = re.compile(r"\b(?:[89]|1\d)\s?\+\s*(?:years|yrs|лет)|\b(?:[89]|1\d)\s+(?:or more|plus)\s+years", I)

# Приоритетные компании: инженерная роль с python в тексте, минус явные исключения
PRIORITY_ROLE_RX = re.compile(r"engineer|developer|programmer|architect|\bswe\b", I)

# Роли, которые показываются со штрафом (если в названии нет Backend/Software)
ROLE_PENALTY_RX = re.compile(
    r"data engineer|analytics engineer|data scientist|ml research\w*|research (?:scientist|engineer)",
    I,
)
# Эксплуатационные роли: профиль по умолчанию — backend-разработка продукта, а не DevOps/SRE; такие
# вакансии скрываются, если в названии нет Backend / Software Engineer / Developer
OPS_ROLE_RX = re.compile(
    r"devops|devsecops|\bsre\b|site reliability|infra(?:structure)? engineer|security (?:engineer|operations|analyst)|"
    r"cloud engineer|systems? engineer|systems? administrator|sysadmin|dataops|mlops|network engineer|"
    r"production engineer|reliability engineer|observability engineer|release engineer|build engineer",
    I,
)
CORE_TITLE_RX = re.compile(
    r"back[\s-]?end|б[эе]к[\s-]?енд|software (?:engineer|developer)|\bdeveloper\b|python|django|fastapi|"
    r"full[\s-]?stack|product engineer|разработчик|программист",
    I,
)
PLATFORM_TITLE_RX = re.compile(r"platform|infrastructure|\binfra\b|internal tools|developer experience|devex", I)
OTHER_LANGS["Rust"] = re.compile(r"\brust\b", I)

# Источники, которые отдают только название/краткое превью — текст вакансии считается недоступным
PREVIEW_ONLY_SOURCES = {"justjoin.it", "remote.com", "remoterocketship", "cryptocurrencyjobs", "bolt"}
