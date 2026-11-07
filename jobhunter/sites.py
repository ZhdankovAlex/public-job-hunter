"""Карьерные страницы без стандартной ATS: Aiven, Bolt, fin.dev, Gurtam, Paychex (примеры адаптеров).

У этих компаний нет публичного JSON API, поэтому читается HTML их собственных
страниц (robots.txt проверяется клиентом Http, лимит — 1 запрос в секунду на хост).
"""
from __future__ import annotations

import html
import json
import logging
import re
from collections.abc import Iterator

from . import config as c
from . import filters
from .http import FetchError, Http
from .models import Job, html_to_text, parse_date

log = logging.getLogger(__name__)

_JSONLD_RX = re.compile(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', re.S)
_NOISE_RX = re.compile(r"<(script|style|svg|nav|header|footer|noscript)\b.*?</\1>", re.S | re.I)


_HTML = {"Accept": "text/html,*/*;q=0.8"}  # некоторые сайты отвечают 404 на Accept: application/json


def _get(http: Http, url: str) -> str:
    """HTML страницы; любой не-200 ответ — FetchError."""
    r = http.get(url, headers=_HTML)
    if r.status_code != 200:
        raise FetchError(f"{url}: HTTP {r.status_code}")
    return r.text


def _page_text(page: str) -> str:
    """Текст страницы без скриптов, навигации и подвала."""
    return html_to_text(_NOISE_RX.sub(" ", page))


def _jsonld(page: str, kind: str) -> dict:
    """Первый JSON-LD объект страницы с нужным @type либо {}."""
    for raw in _JSONLD_RX.findall(page):
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        for item in data if isinstance(data, list) else data.get("@graph", [data]):
            if isinstance(item, dict) and item.get("@type") == kind:
                return item
    return {}


def findev(http: Http) -> Iterator[Job]:
    """fin.dev: список — JSON-LD ItemList, вакансия — JSON-LD JobPosting."""
    listing = _get(http, "https://fin.dev/career/open-positions/")
    urls = [e.get("url") for e in _jsonld(listing, "ItemList").get("itemListElement", [])]
    for url in filter(None, urls):
        posting = _jsonld(_get(http, url), "JobPosting")
        if not posting:
            log.warning("fin.dev: на странице %s нет JobPosting", url)
            continue
        countries = [c.get("name", "") for c in posting.get("applicantLocationRequirements") or []]
        remote = posting.get("jobLocationType") == "TELECOMMUTE"
        yield Job(
            company="fin.dev",
            title=posting.get("title", ""),
            location=("Remote: " if remote else "") + ", ".join(filter(None, countries)),
            url=url,
            posted=parse_date(posting.get("datePosted")),
            description=html_to_text(posting.get("description")),
            remote=remote or None,
            employment=str(posting.get("employmentType") or ""),
            source="fin.dev",
        )


def gurtam(http: Http) -> Iterator[Job]:
    """Gurtam: карточки вакансий на /career, текст — со страницы вакансии."""
    listing = _get(http, "https://gurtam.com/career")
    cards = re.findall(r'<a\s+href="(/career/[^"#?]+)"([^>]*vacancy-card[^>]*)>(.*?)</a>', listing, re.S)
    for path, attrs, body in cards:
        m = re.search(r"<h3[^>]*>(.*?)</h3>", body, re.S)
        title = html_to_text(m.group(1)) if m else ""
        place = re.search(r'data-location="([^"]*)"', attrs)
        url = "https://gurtam.com" + path
        yield Job(
            company="Gurtam",
            title=title,
            location=html.unescape(place.group(1)) if place else "",
            url=url,
            description=_page_text(_get(http, url)),
            source="gurtam",
        )


def bolt(http: Http) -> Iterator[Job]:
    """Bolt: постраничный список; страница вакансии читается только для подходящих названий."""
    seen: set[str] = set()
    for page_no in range(1, 16):
        page = _get(http, "https://bolt.eu/en/careers/positions/" + (f"?page={page_no}" if page_no > 1 else ""))
        items = page.split('data-testid="AccordionItem"')[1:]
        fresh = 0
        for chunk in items:
            chunk = re.sub(r"<(style|svg)\b.*?</\1>", "", chunk, flags=re.S)
            uid = re.search(r"/careers/positions/([0-9a-f-]{36})", chunk)
            labels = [html.unescape(t).strip() for t in re.findall(r">([^<>]{2,80})</(?:span|div|p)>", chunk)]
            if not uid or not labels or uid.group(1) in seen:
                continue
            seen.add(uid.group(1))
            fresh += 1
            title, location = labels[0], labels[2] if len(labels) > 2 else ""
            if not filters.title_candidate(title):
                continue
            url = f"https://bolt.eu/en/careers/positions/{uid.group(1)}/"
            text = _page_text(_get(http, url))
            workplace = re.search(r"WORKPLACE\s+([A-Za-z-]+)", text)
            kind = re.search(r"TYPE\s+([A-Za-z -]+?)\s*(?:\n|View all)", text)
            if workplace and workplace.group(1).lower() != "on-site":
                location = f"{location} ({workplace.group(1)})"
            yield Job(
                company="Bolt",
                title=title,
                location=location,
                url=url,
                description=text,
                remote=bool(workplace and workplace.group(1).lower() == "remote") or None,
                employment=kind.group(1) if kind else "",
                source="bolt",
            )
        if not fresh:
            break


def _turbo_stream(raw: str):
    """Ответ загрузчика React Router (`*.data`, формат turbo-stream): плоский массив со ссылками -> объект.

    Объект {"_<i>": <j>}: ключ — строка arr[i], значение — arr[j]; список — индексы элементов;
    отрицательные индексы — служебные значения (undefined, null…), здесь они None.
    """
    arr = json.loads(raw.splitlines()[0])
    memo: dict[int, object] = {}

    def node(i):
        if not isinstance(i, int) or i < 0:
            return None
        if i not in memo:
            v = arr[i]
            if isinstance(v, list):
                memo[i] = [node(x) for x in v]
            elif isinstance(v, dict):
                memo[i] = {arr[int(k[1:])]: node(x) for k, x in v.items() if k.startswith("_")}
            else:
                memo[i] = v
        return memo[i]

    return node(0)


def aiven(http: Http) -> Iterator[Job]:
    """Aiven: сайт рисует вакансии скриптом, но данные страницы отдаёт загрузчик /careers/job.data
    (публичные ATS-борды Greenhouse под их slug закрыты). Текст — со страницы careers.aiven.io/openings/<id>
    (её /api/ закрыт в robots.txt, страницы вакансий — нет); открывается только для подходящих названий."""
    r = http.get("https://aiven.io/careers/job.data")
    if r.status_code != 200:
        raise FetchError(f"aiven.io/careers/job.data: HTTP {r.status_code}")
    try:
        data = _turbo_stream(r.text)["routes/careers/job"]["data"]
    except (ValueError, KeyError, TypeError, IndexError) as e:
        raise FetchError(f"aiven.io: формат job.data изменился ({e})") from e
    if data.get("jobsUnavailable"):
        raise FetchError("aiven.io: список вакансий недоступен (jobsUnavailable)")
    for item in data.get("jobs") or []:
        title, url = item.get("title") or "", item.get("url") or ""
        if not url or not filters.title_candidate(title):
            continue
        location = item.get("location") or ""
        yield Job(
            company="Aiven",
            title=title,
            location=location,
            url=url,
            description=_page_text(_get(http, url)),
            remote=bool(re.search(r"remote", location, re.I)) or None,
            source="aiven",
        )


def paychex(http: Http) -> Iterator[Job]:
    """Paychex: карьерный сайт на iCIMS Jibe, открытый JSON /api/jobs с текстом вакансии (robots.txt: crawl-delay 5).
    Вакансий немного (~60, в основном продажи в США) — читаем все, роль и локацию отсеют фильтры."""
    seen, page = 0, 1
    while page <= 5:
        r = http.get("https://careers.paychex.com/api/jobs", params={"page": page, "limit": 100})
        if r.status_code != 200:
            raise FetchError(f"careers.paychex.com/api/jobs: HTTP {r.status_code}")
        data = r.json()
        items = data.get("jobs") or []
        for item in items:
            j = item.get("data") or {}
            if not filters.title_candidate(j.get("title") or ""):
                continue
            text = " ".join(html_to_text(j.get(k)) for k in ("description", "responsibilities", "qualifications") if j.get(k))
            yield Job(
                company="Paychex",
                title=j.get("title") or "",
                location=j.get("full_location") or ", ".join(filter(None, [j.get("city"), j.get("country")])),
                url=f"https://careers.paychex.com/careers/jobs/{j.get('req_id')}?lang=en-us",
                posted=parse_date(j.get("posted_date")),
                description=text,
                employment=(j.get("employment_type") or "").replace("_", " ").lower(),
                source="paychex",
            )
        seen += len(items)
        if not items or seen >= int(data.get("totalCount") or 0):
            break
        page += 1


SOURCES = {"aiven": aiven, "bolt": bolt, "fin.dev": findev, "gurtam": gurtam, "paychex": paychex}


def enabled_sources() -> list[str]:
    """Источники из SOURCES без выключенных в settings.toml [sources].disabled."""
    from . import geo

    disabled = {n.lower() for n in geo.S.get("sources", {}).get("disabled", [])}
    return [name for name in SOURCES if name.lower() not in disabled]


def fetch(name: str, http: Http) -> list[Job]:
    """Собирает что успеет; при ошибке возвращает уже полученное и пишет в errors.log."""
    jobs: list[Job] = []
    try:
        for job in SOURCES[name](http):
            jobs.append(job)
    except FetchError as e:
        log.warning("сайт %s: %s", name, e)
    return jobs


# --- Доски вакансий: Remote Rocketship, Brenxor, justjoin.it, remote.com, Cryptocurrency Jobs ---

_NEXT_DATA_RX = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
_NEXT_F_RX = re.compile(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)</script>', re.S)


def _boards() -> dict:
    """Секция [boards] из settings.toml."""
    from . import geo  # импорт здесь: geo читает settings.toml при загрузке

    return geo.S.get("boards", {})


def _next_payload(page: str) -> str:
    """Склеенный поток данных Next.js (self.__next_f.push) со страницы."""
    parts = _NEXT_F_RX.findall(page)
    try:
        return json.loads('"' + "".join(parts) + '"') if parts else ""
    except ValueError:
        return ""


def _array_after(payload: str, marker: str) -> list:
    """JSON-массив, который начинается сразу после marker в потоке данных."""
    start = payload.find(marker)
    if start < 0:
        return []
    try:
        return json.JSONDecoder().raw_decode(payload[payload.find("[", start):])[0]
    except ValueError:
        return []


def remoterocketship(http: Http) -> Iterator[Job]:
    """Remote Rocketship: вакансии лежат в __NEXT_DATA__ страницы поиска; url — оригинал в ATS."""
    cfg = _boards()
    base = cfg.get("remoterocketship_url", "")
    if not base:
        return
    for page_no in range(1, int(cfg.get("remoterocketship_pages", 5)) + 1):
        page = _get(http, f"{base}&page={page_no}")
        m = _NEXT_DATA_RX.search(page)
        if not m:
            raise FetchError("remoterocketship: на странице нет __NEXT_DATA__ (изменилась вёрстка)")
        openings = json.loads(m.group(1))["props"]["pageProps"].get("initialJobOpenings") or []
        if not openings:
            break
        for j in openings:
            kind = (j.get("locationType") or "").lower()
            countries = j.get("locationCountries") or []
            place = ", ".join(filter(None, [j.get("locationCity"), j.get("location")]))
            if isinstance(countries, list) and countries:
                place = "; ".join(dict.fromkeys([place, *map(str, countries)]))
            salary = j.get("salaryRange")
            yield Job(
                company=(j.get("company") or {}).get("name") or "",
                title=j.get("roleTitle") or "",
                location=f"{place} ({kind})" if kind in ("remote", "hybrid") else place,
                url=j.get("url") or "",
                posted=parse_date(j.get("created_at")),
                description="\n".join(filter(None, [
                    j.get("twoLineJobDescriptionSummary") or j.get("jobDescriptionSummary"),
                    html_to_text(j.get("roleDescription")),
                    html_to_text(j.get("roleRequirements")),
                    "Skills: " + ", ".join(j.get("techStack") or []),
                ])),
                remote=(kind == "remote") or None,
                employment=j.get("employmentType") or "",
                salary=salary if isinstance(salary, str) else "",
                source="remoterocketship",
            )


def brenxor(http: Http) -> Iterator[Job]:
    """Brenxor: список — карточки, полный текст — JSON-LD JobPosting на странице вакансии."""
    cfg = _boards()
    base = cfg.get("brenxor_url", "")
    if not base:
        return
    budget = int(cfg.get("brenxor_max_details", 40))
    seen: set[str] = set()
    for page_no in range(1, int(cfg.get("brenxor_pages", 4)) + 1):
        r = http.get(base + (f"?page={page_no}" if page_no > 1 else ""))
        if r.status_code == 404 and page_no > 1:
            break  # страницы списка закончились
        if r.status_code != 200:
            raise FetchError(f"brenxor: HTTP {r.status_code}")
        cards = r.text.split('<a class="job-listing-details"')[1:]
        fresh = 0
        for card in cards:
            link = re.search(r'href="(https://brenxor\.com/job-details-\d+[^"]*)"', card)
            title = re.search(r'<h3 class="job-listing-title">(.*?)</h3>', card, re.S)
            if not link or not title or link.group(1) in seen:
                continue
            seen.add(link.group(1))
            fresh += 1
            title_text = html_to_text(title.group(1))
            if budget <= 0 or not filters.title_candidate(title_text):
                continue
            budget -= 1
            place = re.search(r'icon-material-outline-location-on"></i>(.*?)</li>', card, re.S)
            company = re.search(r'icon-material-outline-business"></i>(.*?)</a>', card, re.S)
            posting = _jsonld(_get(http, link.group(1)), "JobPosting")
            yield Job(
                company=(posting.get("hiringOrganization") or {}).get("name")
                or (html_to_text(company.group(1)) if company else ""),
                title=posting.get("title") or title_text,
                location=re.sub(r"\s*\+\d+ regions?\s*", "; ", " ".join(html_to_text(place.group(1)).split()))
                if place else "Remote",
                url=link.group(1),
                posted=parse_date(posting.get("datePosted")),
                description=html_to_text(posting.get("description")),
                remote=True,
                employment=str(posting.get("employmentType") or ""),
                source="brenxor",
            )
        if not fresh:
            break


def justjoin(http: Http) -> Iterator[Job]:
    """justjoin.it: вакансии в потоке данных страницы списка (без текста — только название и навыки)."""
    url = _boards().get("justjoin_url", "")
    if not url:
        return
    offers = _array_after(_next_payload(_get(http, url)), '"offers":[{"applyUrl"')
    if not offers:
        raise FetchError("justjoin.it: вакансии на странице не найдены (изменилась вёрстка)")
    for o in offers:
        cities = list(dict.fromkeys(
            filter(None, [o.get("city"), *(m.get("city") for m in o.get("multilocation") or [])])
        ))
        kind = o.get("workplaceType") or ""
        skills = [*(o.get("requiredSkills") or []), *(o.get("niceToHaveSkills") or [])]
        yield Job(
            company=o.get("companyName") or "",
            title=o.get("title") or "",
            location=", ".join(cities) + ", Poland" + (f" ({kind})" if kind in ("remote", "hybrid") else ""),
            url=f"https://justjoin.it/job-offer/{o.get('slug')}",
            posted=parse_date(o.get("publishedAt")),
            description=(o.get("title") or "") + "\nSkills: " + ", ".join(map(str, skills)),
            remote=(kind == "remote") or None,
            employment=o.get("workingTime") or "",
            source="justjoin.it",
        )


def remotecom(http: Http) -> Iterator[Job]:
    """remote.com/jobs: поиск по запросу; в списке есть название, компания и страны найма."""
    cfg = _boards()
    query = cfg.get("remotecom_query", "")
    if not query:
        return
    for page_no in range(1, int(cfg.get("remotecom_pages", 3)) + 1):
        url = f"https://remote.com/jobs/all?query={query}" + (f"&page={page_no}" if page_no > 1 else "")
        jobs = _array_after(_next_payload(_get(http, url)), '"jobs":[{"status"')
        if not jobs:
            break
        for j in jobs:
            hiring = j.get("hiringLocation") or {}
            places = [(loc.get("value") or {}).get("name", "") for loc in hiring.get("includedLocations") or []]
            remote = (j.get("workplaceLocation") or {}).get("type") == "remote"
            company = j.get("companyProfile") or {}
            location = ", ".join(filter(None, places)) or ("Remote" if remote else "")
            yield Job(
                company=company.get("name") or "",
                title=j.get("title") or "",
                location=f"Remote: {location}" if remote and places else location,
                url=f"https://remote.com/jobs/{company.get('slug')}/{j.get('slug')}",
                posted=parse_date(j.get("publishedAt")),
                description=(j.get("title") or "")
                + ("\nWe offer visa sponsorship." if j.get("visaSponsorshipOffered") else ""),
                remote=remote or None,
                employment=j.get("employmentType") or "",
                source="remote.com",
            )


def cryptocurrencyjobs(http: Http) -> Iterator[Job]:
    """Cryptocurrency Jobs: общий RSS-фид (последние вакансии всех категорий)."""
    import xml.etree.ElementTree as ET
    from email.utils import parsedate_to_datetime

    try:
        root = ET.fromstring(_get(http, "https://cryptocurrencyjobs.co/index.xml"))
    except ET.ParseError as e:
        raise FetchError(f"cryptocurrencyjobs: не удалось разобрать RSS ({e})")
    for item in root.iter("item"):
        title, _, company = (item.findtext("title") or "").rpartition(" at ")
        text = html_to_text(item.findtext("description"))
        if re.search(r"no geographical restrictions|from anywhere", text, re.I):
            location = "Remote, Worldwide"
        elif re.search(r"remote", text, re.I):
            place = re.search(r"remote (?:in|from|within|across) ([^.]{3,60})", text, re.I)
            location = f"Remote - {place.group(1).strip()}" if place else "Remote"
        else:
            location = ""
        try:
            posted = parsedate_to_datetime(item.findtext("pubDate") or "")
        except (TypeError, ValueError):
            posted = None
        yield Job(
            company=company.strip(),
            title=title.strip() or (item.findtext("title") or ""),
            location=location,
            url=item.findtext("link") or "",
            posted=posted,
            description=text,
            remote=bool(location) or None,
            employment="part-time" if re.search(r"part-time position", text, re.I) else "",
            source="cryptocurrencyjobs",
        )



def rivian(http: Http) -> Iterator[Job]:
    """Rivian: карьерный сайт на iCIMS/Jibe отдаёт вакансии JSON-ом (/api/jobs), текст — в ответе."""
    seen = 0
    for page_no in range(1, 11):
        r = http.get("https://careers.rivian.com/api/jobs", params={"page": page_no, "limit": 100})
        if r.status_code != 200:
            raise FetchError(f"rivian: HTTP {r.status_code}")
        data = r.json()
        items = data.get("jobs") or []
        for item in items:
            j = item.get("data", item)
            place = ", ".join(filter(None, [j.get("city"), j.get("country")]))
            yield Job(
                company="Rivian",
                title=j.get("title") or "",
                location=place,
                url=f"https://careers.rivian.com/careers-home/jobs/{j.get('slug')}",
                posted=parse_date(j.get("posted_date")),
                description="\n".join(filter(None, [
                    html_to_text(j.get("description")), html_to_text(j.get("responsibilities")),
                    html_to_text(j.get("qualifications")),
                ])),
                employment=j.get("employment_type") or "",
                source="rivian",
            )
        seen += len(items)
        if not items or seen >= int(data.get("totalCount") or 0):
            break


_HABR = "https://career.habr.com"
_SSR_RX = re.compile(r'<script type="application/json" data-ssr-state="true">(.*?)</script>', re.S)
HABR_PYTHON_SKILL = 446  # skills[]=446 — Python
HABR_MAX_PAGES = 6


def habr_career(http: Http) -> Iterator[Job]:
    """Habr Career (career.habr.com): remote-вакансии с навыком Python, свежие первыми (данные — SSR-JSON страницы)."""
    for page_no in range(1, HABR_MAX_PAGES + 1):
        page = _get(http, f"{_HABR}/vacancies?skills[]={HABR_PYTHON_SKILL}&remote=true&type=all"
                          f"&sort=date&page={page_no}")
        m = _SSR_RX.search(page)
        if not m:
            raise FetchError("habr career: нет SSR-данных (изменилась вёрстка)")
        data = json.loads(html.unescape(m.group(1))).get("vacancies") or {}
        for item in data.get("list") or []:
            title = item.get("title") or ""
            if item.get("archived") or not filters.title_candidate(title) or c.TG_EXCLUDE_RX.search(title):
                continue
            url = _HABR + item["href"]
            try:
                detail = _get(http, url)
            except FetchError as e:
                log.warning("habr career %s: %s", url, e)
                detail = ""
            i = detail.find("vacancy-description__text")
            body = html_to_text(detail[detail.find(">", i) + 1: detail.find("basic-section", i)]) if i != -1 else ""
            places = [x["title"] for x in item.get("locations") or []]
            skills = ", ".join(x["title"] for x in item.get("skills") or [])
            salary = (item.get("salary") or {}).get("formatted") or ""
            yield Job(
                company=(item.get("company") or {}).get("title") or "—",
                title=title,
                location=", ".join(["Remote"] if item.get("remoteWork") else places),
                url=url,
                posted=parse_date((item.get("publishedDate") or {}).get("date")),
                description=f"{body}\nНавыки: {skills}" if body else "",
                remote=bool(item.get("remoteWork")),
                employment=(item.get("employment") or "").replace("_", "-"),
                salary=salary,
                source="habr career",
            )
        meta = data.get("meta") or {}
        if page_no >= int(meta.get("totalPages") or 0):
            break


_ZERO_ONE = "https://01.tech"
_ZERO_ONE_ID_RX = re.compile(r'"([a-z0-9]{24})"')
_ZERO_ONE_FORMAT_RX = re.compile(r"\b(Remote|Hybrid|On site)\b")


def zero_one_tech(http: Http) -> Iterator[Job]:
    """01.tech (iGaming): список подгружается кнопкой «Load more», но id всех вакансий есть в данных страницы.

    Открываем каждую по id; название — из <title>, формат (Remote / Hybrid / On site) — из шапки.
    Город офиса на сайте не указан, релокация — после испытательного срока.
    """
    page = _get(http, f"{_ZERO_ONE}/vacancies")
    for vid in dict.fromkeys(_ZERO_ONE_ID_RX.findall(page)):
        url = f"{_ZERO_ONE}/vacancies/{vid}"
        try:
            detail = _get(http, url)
        except FetchError:
            continue  # не всякий id в данных страницы — вакансия
        m = re.search(r"<title>\s*Vacancy:\s*(.*?)\s*</title>", detail, re.S)
        if not m:
            continue
        title = html.unescape(m.group(1)).strip()
        if not filters.title_candidate(title):
            continue
        text = _page_text(detail)
        fmt = _ZERO_ONE_FORMAT_RX.search(text[:600])
        work = fmt.group(1) if fmt else ""
        yield Job(
            company="01.tech", title=title, location="Remote" if work == "Remote" else work.lower(),
            url=url, description=text, remote=work == "Remote", source="01.tech",
        )


_RELOCATE_ME = "https://relocate.me"
_RELOCATE_LINK_RX = re.compile(r'<a[^>]*href="(/([a-z-]+)/([a-z-]+)/([a-z0-9-]+)/[a-z0-9-]+-\d+)"[^>]*>(.*?)</a>', re.S)
RELOCATE_ME_PAGES = 5


def relocate_me(http: Http) -> Iterator[Job]:
    """relocate.me: вакансии с релокацией и визовой поддержкой; поиск по «python», ссылка = /страна/город/компания/…"""
    seen = set()
    for page_no in range(1, RELOCATE_ME_PAGES + 1):
        page = _get(http, f"{_RELOCATE_ME}/international-jobs?query=python&page={page_no}")
        links = _RELOCATE_LINK_RX.findall(page)
        fresh = [x for x in links if x[0] not in seen]
        if not fresh:
            break
        for path, country, city, company, inner in fresh:
            seen.add(path)
            title = re.sub(r"\s+in\s+[A-Z][\w\s-]*$", "", html_to_text(inner).strip())
            if not filters.title_candidate(title):
                continue
            try:
                detail = _get(http, _RELOCATE_ME + path)
            except FetchError as e:
                log.warning("relocate.me %s: %s", path, e)
                continue
            place = ", ".join(w.replace("-", " ").title() for w in (city, country) if w != "remote") or "Remote"
            yield Job(
                company=company.replace("-", " ").title(), title=title, location=place,
                url=_RELOCATE_ME + path, description=_page_text(detail)[:20000],
                remote=country == "remote", source="relocate.me",
            )


def manual_jobs(http: Http) -> Iterator[Job]:
    """Отдельные вакансии из settings.toml [manual_jobs].urls — найденные руками, открываются каждый прогон.

    Данные: JSON-LD JobPosting, иначе данные Next.js (pageProps.position), иначе
    <title> и текст страницы. Компания — из JSON-LD или из домена (example.com -> Example).
    """
    from urllib.parse import urlsplit

    from . import geo

    for url in geo.S.get("manual_jobs", {}).get("urls", []):
        try:
            page = _get(http, url)
        except FetchError as e:
            log.warning("manual_jobs %s: %s", url, e)
            continue
        domain = urlsplit(url).netloc.removeprefix("www.").split(".")
        company = (domain[-2] if len(domain) >= 2 else domain[0]).capitalize()
        title, location, text, remote = "", "", "", None
        ld = _jsonld(page, "JobPosting")
        if ld:
            title = ld.get("title") or ""
            company = (ld.get("hiringOrganization") or {}).get("name") or company
            places = ld.get("jobLocation") or []
            places = places if isinstance(places, list) else [places]
            location = "; ".join(filter(None, (
                ", ".join(filter(None, [(p.get("address") or {}).get("addressLocality"),
                                        (p.get("address") or {}).get("addressCountry")]))
                for p in places if isinstance(p, dict))))
            if ld.get("jobLocationType") == "TELECOMMUTE":
                remote, location = True, "; ".join(filter(None, ["Remote", location]))
            text = html_to_text(html.unescape(ld.get("description") or ""))
        else:
            m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', page, re.S)
            position = {}
            if m:
                try:
                    position = (json.loads(m.group(1)).get("props", {}).get("pageProps", {}).get("position") or {})
                except ValueError:
                    position = {}
            if position:
                title = position.get("text") or position.get("title") or ""
                names = [p.get("name", "") for p in position.get("locations") or [] if isinstance(p, dict)]
                location = "; ".join(names)
                remote = any(p.get("type") == "remote" for p in position.get("locations") or [] if isinstance(p, dict))
                text = html_to_text(position.get("description") or "")
            else:
                t = re.search(r"<title>(.*?)</title>", page, re.S)
                title = html.unescape(t.group(1)).split("|")[0].strip() if t else url
                text = _page_text(page)
        yield Job(company=company, title=title, location=location, url=url, description=text[:20000],
                  remote=remote, source="вручную")


_HIREHI = "https://hirehi.ru"
HIREHI_SECTIONS = ("/vacancies/python", "/vacancies/backend")


def hirehi(http: Http) -> Iterator[Job]:
    """HireHi: список раздела — JSON-LD ItemList на странице, вакансия — JSON-LD JobPosting (API закрыт в robots.txt)."""
    seen = set()
    for section in HIREHI_SECTIONS:
        page = _get(http, _HIREHI + section)
        items = []
        for raw in _JSONLD_RX.findall(page):
            try:
                data = json.loads(raw)
            except ValueError:
                continue
            if isinstance(data, dict) and data.get("@type") == "ItemList":
                items = data.get("itemListElement") or []
        for item in items:
            url, title = item.get("url") or "", item.get("name") or ""
            if not url or url in seen or not filters.title_candidate(title):
                continue
            seen.add(url)
            try:
                ld = _jsonld(_get(http, url), "JobPosting")
            except FetchError as e:
                log.warning("hirehi %s: %s", url, e)
                continue
            if not ld:
                continue
            address = ((ld.get("jobLocation") or {}).get("address") or {}) if isinstance(ld.get("jobLocation"), dict) else {}
            place = ", ".join(filter(None, [address.get("addressLocality"), address.get("addressCountry")]))
            text = html_to_text(html.unescape(ld.get("description") or ""))
            remote = ld.get("jobLocationType") == "TELECOMMUTE" or bool(re.search(r"удал[её]нн|remote", place, re.I))
            pay = ld.get("baseSalary") or {}
            amount = (pay.get("value") or {}).get("value") if isinstance(pay.get("value"), dict) else pay.get("value")
            unit = ((pay.get("value") or {}).get("unitText") or "") if isinstance(pay.get("value"), dict) else ""
            salary = f"{amount} {pay.get('currency', '')}{' в год' if unit == 'YEAR' else ''}" if amount else ""
            yield Job(
                company=(ld.get("hiringOrganization") or {}).get("name") or "—", title=ld.get("title") or title,
                location=place or ("Remote" if remote else ""), url=url, posted=parse_date(ld.get("datePosted")),
                description=text, remote=remote or None, salary=salary,
                employment=(ld.get("employmentType") or "").replace("_", "-").lower(), source="hirehi",
            )


SOURCES.update({
    "hirehi": hirehi,
    "manual": manual_jobs,
    "relocate.me": relocate_me,
    "01.tech": zero_one_tech,
    "habr career": habr_career,
    "rivian": rivian,
    "remoterocketship": remoterocketship,
    "brenxor": brenxor,
    "justjoin.it": justjoin,
    "remote.com": remotecom,
    "cryptocurrencyjobs": cryptocurrencyjobs,
})


# --- Карьерные страницы компаний без поддерживаемой ATS (settings.toml [career_pages]) ------

PAGE_STATUS: dict[str, dict] = {}  # компания -> что удалось прочитать (для отчёта о доступности)
PAGE_DETAIL_LIMIT = 100  # предохранитель от неверно разобранной страницы; самые подходящие — первыми
_ANCHOR_RX = re.compile(r'<a\s([^>]*?href="([^"#]+)"[^>]*)>(.*?)</a>', re.S | re.I)
_LABEL_RX = re.compile(r'(?:aria-label|title)="([^"]+)"', re.I)


# текст ссылки, который не название вакансии; тогда название — из адреса (/job/<slug>, /careers/<slug>)
_CORE_TITLE_RX = re.compile(r"python|django|fastapi|back[\s-]?end|бэкенд|бекенд", re.I)
_GENERIC_LINK_RX = re.compile(r"^(?:apply(?: now)?|learn more|read more|details|view(?: job| role)?|more|подробнее|откликнуться|смотреть)\W*$", re.I)
_JOB_SLUG_RX = re.compile(r"/(?:jobs?|careers?|positions?|vacanc(?:y|ies)|openings?)/([a-z0-9][a-z0-9_-]{3,90})/?(?:[?#]|$)", re.I)


def career_page(http: Http, company, url: str, location: str = "") -> list[Job]:
    """Ищет на карьерной странице ссылки на вакансии подходящих ролей и читает их текст.

    Работает только для страниц, которые отдают список вакансий в HTML; если список
    строится скриптами, ссылок не будет — это видно в отчёте о доступности.
    location — метка по умолчанию, если на странице вакансии нет ни Remote, ни домашней страны
    (например, «Remote» для компании, которая целиком работает удалённо).
    """
    from urllib.parse import urljoin

    from . import config as c

    status = PAGE_STATUS.setdefault(company.name, {"links": 0, "jobs": 0, "error": ""})
    try:
        r = http.get(url, timeout=25, headers=_HTML)
    except FetchError as e:
        status["error"] = str(e)[:120]
        return []
    if r.status_code != 200:
        status["error"] = f"HTTP {r.status_code}"
        return []
    candidates: dict[str, str] = {}
    for attrs, href, inner in _ANCHOR_RX.findall(r.text):
        lines = [line.strip() for line in html_to_text(inner).split("\n") if line.strip()]
        label = _LABEL_RX.search(attrs)
        # ссылка-кнопка без текста: название вакансии есть только в aria-label / title («Должность, Отдел»)
        title = lines[0] if lines else (html.unescape(label.group(1)).split(",")[0].strip() if label else "")
        if _GENERIC_LINK_RX.match(title):
            # кнопка «Apply», а название — в соседнем блоке: берём из адреса /job/senior-python-developer
            slug = _JOB_SLUG_RX.search(href)
            title = slug.group(1).replace("-", " ").replace("_", " ").strip().title() if slug else ""
        if 5 <= len(title) <= 110 and filters.title_candidate(title):
            candidates.setdefault(urljoin(r.url, href), title)
    status["links"] = len(candidates)
    # сначала Python / backend, потом остальные инженерные роли — при обрезке лимитом теряются наименее подходящие
    ranked = sorted(candidates.items(), key=lambda kv: (0 if _CORE_TITLE_RX.search(kv[1]) else 1))
    if len(ranked) > PAGE_DETAIL_LIMIT:
        log.warning("%s: открыто %d из %d подходящих вакансий на карьерной странице — остальные пропущены "
                    "(лимит PAGE_DETAIL_LIMIT в sites.py)", company.name, PAGE_DETAIL_LIMIT, len(ranked))
    jobs: list[Job] = []
    for link, title in ranked[:PAGE_DETAIL_LIMIT]:
        try:
            page = http.get(link, timeout=25, headers=_HTML)
        except FetchError:
            continue
        if page.status_code != 200:
            continue
        text = _page_text(page.text)[:20000]
        head = text[:4000]
        places = []
        if c.HOME_COUNTRY and c.HOME_RX.search(head):
            places.append(c.HOME_COUNTRY)
        if c.REMOTE_RX.search(head):
            places.append("Remote")
        if not places and location:
            places.append(location)
        jobs.append(Job(
            company=company.name, title=title, location="; ".join(places), url=link,
            description=text, remote=bool(c.REMOTE_RX.search(" ".join(places))) or None,
            source="career-page", score=company.score, note=company.note,
        ))
    status["jobs"] = len(jobs)
    return jobs


# --- Крупные компании (jobhunter/bigtech.py): Microsoft, Workday-сайты, Oracle, Amazon ----
from .bigtech import SOURCES as BIGTECH_SOURCES  # noqa: E402

SOURCES.update(BIGTECH_SOURCES)
