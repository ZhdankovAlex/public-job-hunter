"""Ключи для сравнения: ссылка на вакансию, ID вакансии в ссылке, название компании, борд ATS по ссылке."""
import re
from urllib.parse import parse_qsl, urlsplit

_ID_RX = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|\d{7,}", re.I)
_ID_PARAMS = {"gh_jid", "jid", "id", "job", "jobid", "job_id", "vacancy"}


def norm_url(url):
    parts = urlsplit(url.strip())
    host = parts.netloc.lower().removeprefix("www.")
    # utm-метки отбрасываем, но параметр с ID вакансии — часть адреса
    ids = sorted(f"{k.lower()}={v}" for k, v in parse_qsl(parts.query) if k.lower() in _ID_PARAMS)
    return host + parts.path.rstrip("/").lower() + ("?" + "&".join(ids) if ids else "")


def url_ids(url):
    """ID вакансии внутри URL (uuid или длинное число) — переживает смену домена."""
    return {m.lower() for m in _ID_RX.findall(url)}


_TRANSLIT = dict(zip(
    "абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
    ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s",
     "t", "u", "f", "kh", "ts", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya"],
))
_LEGAL_RX = re.compile(r"(?<!\w)(?:ооо|оао|зао|пао|ао|ип|гк|inc|llc|ltd|gmbh|corp)(?!\w)\.?", re.I)


def company_key(name):
    """'ООО Яндекс' -> 'yandex', 'Т-Банк' -> 'tbank': без юр. форм, пунктуации, в латинице."""
    text = _LEGAL_RX.sub(" ", name.lower())
    text = "".join(_TRANSLIT.get(ch, ch) for ch in text)
    key = re.sub(r"[^a-z0-9]", "", text).replace("ks", "x")
    return key


# Прямая ссылка на борд -> (platform, slug)
_ATS_LINK_RXS = (
    ("greenhouse", re.compile(
        r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board(?:/js)?\?for=)?([\w-]+)|"
        r"boards-api\.greenhouse\.io/v1/boards/([\w-]+)", re.I)),
    ("lever", re.compile(r"jobs(?:\.eu)?\.lever\.co/([\w-]+)|api\.lever\.co/v0/postings/([\w-]+)", re.I)),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([\w.-]+)|api\.ashbyhq\.com/posting-api/job-board/([\w.-]+)", re.I)),
    ("workable", re.compile(r"apply\.workable\.com/([\w-]+)|https?://([\w-]+)\.workable\.com", re.I)),
    ("teamtailor", re.compile(r"https?://([\w-]+)\.teamtailor\.com", re.I)),
    ("recruitee", re.compile(r"https?://([\w-]+)\.recruitee\.com", re.I)),
    ("bamboohr", re.compile(r"https?://([\w-]+)\.bamboohr\.com", re.I)),
    ("breezy", re.compile(r"https?://([\w-]+)\.breezy\.hr", re.I)),
)
_NOT_A_SLUG = {"embed", "jobs", "www", "app", "apply", "boards", "job-boards", "api", "careers", "help",
               "support", "j", "v1", "widget", "static", "cdn", "assets", "blog", "resources",
               "assets-cdn", "careers-analytics", "attachments", "gallery", "marketing", "status", "developers"}


def ats_from_link(url: str) -> tuple[str, str] | None:
    """(platform, slug), если ссылка ведёт прямо на поддерживаемую ATS."""
    for platform, rx in _ATS_LINK_RXS:
        for m in rx.finditer(url or ""):
            slug = next((g for g in m.groups() if g), "")
            if slug and slug.lower() not in _NOT_A_SLUG:
                return platform, slug
    return None
