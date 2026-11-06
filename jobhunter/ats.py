"""Публичные JSON API четырёх ATS + поиск и кэширование slug'ов компаний."""
import json
import re
import logging
import threading
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from datetime import date

from . import config, geo
from .companies import slug_candidates
from .http import FetchError
from .models import Job, html_to_text, parse_date

log = logging.getLogger(__name__)


def _greenhouse(data, company):
    for j in data.get("jobs", []):
        yield Job(
            company=company,
            title=j.get("title", ""),
            location=(j.get("location") or {}).get("name") or "",
            url=j.get("absolute_url", ""),
            posted=parse_date(j.get("first_published") or j.get("updated_at")),
            description=html_to_text(j.get("content")),
            source="greenhouse",
        )


def _lever(data, company):
    for j in data if isinstance(data, list) else []:
        cats = j.get("categories") or {}
        locations = cats.get("allLocations") or [cats.get("location") or ""]
        parts = [j.get("descriptionPlain") or ""]
        parts += [f"{l.get('text', '')}\n{html_to_text(l.get('content'))}" for l in j.get("lists") or []]
        parts.append(j.get("additionalPlain") or "")
        yield Job(
            company=company,
            title=j.get("text", ""),
            location="; ".join(filter(None, locations)),
            url=j.get("hostedUrl", ""),
            posted=parse_date(j.get("createdAt")),
            description="\n".join(parts),
            remote=(j.get("workplaceType") == "remote") or None,
            employment=cats.get("commitment") or "",
            source="lever",
        )


def _ashby(data, company):
    for j in data.get("jobs", []):
        if j.get("isListed") is False:
            continue
        locations = [j.get("location") or ""]
        locations += [s.get("location", "") for s in j.get("secondaryLocations") or []]
        yield Job(
            company=company,
            title=j.get("title", ""),
            location="; ".join(filter(None, locations)),
            url=j.get("jobUrl", ""),
            posted=parse_date(j.get("publishedAt")),
            description=j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml")),
            # isRemote бывает true и у Hybrid — workplaceType точнее
            remote=(j["workplaceType"] == "Remote") if j.get("workplaceType") else j.get("isRemote"),
            employment=j.get("employmentType") or "",
            source="ashby",
        )


def _workable(data, company):
    for j in data.get("jobs", []):
        place = ", ".join(filter(None, [j.get("city"), j.get("state"), j.get("country")]))
        yield Job(
            company=company,
            title=j.get("title", ""),
            location=place,
            url=j.get("url") or j.get("shortlink") or "",
            posted=parse_date(j.get("published_on") or j.get("created_at")),
            description=html_to_text(j.get("description")),
            remote=j.get("telecommuting"),
            employment=j.get("employment_type") or "",
            source="workable",
        )


def _teamtailor(xml_text, company):
    """Публичный RSS-фид Teamtailor: {slug}.teamtailor.com/jobs.rss"""
    for item in ET.fromstring(xml_text).iter("item"):
        places = []
        for loc in item.findall(".//{*}location"):
            city, country = loc.findtext("{*}city") or "", loc.findtext("{*}country") or ""
            places.append(", ".join(filter(None, [city, country])) or loc.findtext("{*}name") or "")
        status = (item.findtext("remoteStatus") or "").lower()
        location = "; ".join(dict.fromkeys(filter(None, places)))
        if status in ("hybrid", "fully"):
            location += " (hybrid)" if status == "hybrid" else " (remote)"
        try:
            posted = parsedate_to_datetime(item.findtext("pubDate") or "")
        except (TypeError, ValueError):
            posted = None
        yield Job(
            company=company,
            title=item.findtext("title") or "",
            location=location,
            url=item.findtext("link") or "",
            posted=posted,
            description=html_to_text(item.findtext("description")),
            remote=(status == "fully") or None,
            source="teamtailor",
        )


def _recruitee(data, company):
    """Recruitee: {slug}.recruitee.com/api/offers/ — полный текст в ответе."""
    for j in data.get("offers", []):
        place = ", ".join(filter(None, [j.get("city"), j.get("country")])) or j.get("location") or ""
        remote = bool(j.get("remote"))
        yield Job(
            company=company,
            title=j.get("title", ""),
            location=f"{place} (remote)" if remote and "remote" not in place.lower() else place,
            url=j.get("careers_url") or j.get("careers_apply_url") or "",
            posted=parse_date(j.get("published_at") or j.get("created_at")),
            description=html_to_text((j.get("description") or "") + "\n" + (j.get("requirements") or "")),
            remote=remote or None,
            employment=j.get("employment_type_code") or "",
            source="recruitee",
        )


def _bamboohr(data, company, slug=""):
    """BambooHR: список без текста; описание дозапрашивается для подходящих названий."""
    for j in data.get("result", []):
        loc = j.get("atsLocation") or j.get("location") or {}
        place = ", ".join(filter(None, [loc.get("city"), loc.get("state"), loc.get("country")]))
        status = j.get("employmentStatusLabel") or ""
        remote = bool(j.get("isRemote")) or "remote" in status.lower()
        yield Job(
            company=company,
            title=j.get("jobOpeningName", ""),
            location=(place + " (remote)").strip() if remote else place,
            url=f"https://{slug}.bamboohr.com/careers/{j.get('id')}",
            description="",
            remote=remote or None,
            employment=status,
            source="bamboohr",
        )


def _breezy(data, company):
    """Breezy: {slug}.breezy.hr/json — список без текста; описание берётся со страницы вакансии."""
    for j in data if isinstance(data, list) else []:
        loc = j.get("location") or {}
        country = (loc.get("country") or {}).get("name") if isinstance(loc.get("country"), dict) else loc.get("country")
        place = loc.get("name") or ", ".join(filter(None, [loc.get("city"), country]))
        remote = bool(loc.get("is_remote"))
        yield Job(
            company=company,
            title=j.get("name", ""),
            location=f"{place} (remote)" if remote and "remote" not in (place or "").lower() else (place or ""),
            url=j.get("url", ""),
            posted=parse_date(j.get("published_date")),
            description="",
            remote=remote or None,
            employment=(j.get("type") or {}).get("name", "") if isinstance(j.get("type"), dict) else "",
            source="breezy",
        )


# platform -> (url, params, parser)
PLATFORMS = {
    "greenhouse": ("https://boards-api.greenhouse.io/v1/boards/{slug}/jobs", {"content": "true"}, _greenhouse),
    "lever": ("https://api.lever.co/v0/postings/{slug}", {"mode": "json"}, _lever),
    "ashby": ("https://api.ashbyhq.com/posting-api/job-board/{slug}", None, _ashby),
    "workable": ("https://apply.workable.com/api/v1/widget/accounts/{slug}", {"details": "true"}, _workable),
    "teamtailor": ("https://{slug}.teamtailor.com/jobs.rss", None, _teamtailor),
    "recruitee": ("https://{slug}.recruitee.com/api/offers/", None, _recruitee),
    "bamboohr": ("https://{slug}.bamboohr.com/careers/list", None, _bamboohr),
    "breezy": ("https://{slug}.breezy.hr/json", None, _breezy),
}
DETAIL_FETCH_LIMIT = 8  # сколько страниц вакансий открывать на компанию (BambooHR, Breezy)
# Teamtailor — отдельный хост на каждую компанию, поэтому slug не подбирается автоматически:
# такие борды вписываются в ats_mapping.json вручную.
DISCOVERABLE = ("greenhouse", "lever", "ashby", "workable")


class AtsClient:
    def __init__(self, http, mapping_path=config.MAPPING_FILE):
        self.http = http
        self.path = mapping_path
        self.lock = threading.Lock()
        self.mapping = {}
        self.failures = {}  # company -> причина, по которой сегодня нет вакансий
        if mapping_path.exists():
            self.mapping = json.loads(mapping_path.read_text(encoding="utf-8"))

    def save(self):
        with self.lock:
            text = json.dumps(self.mapping, ensure_ascii=False, indent=2, sort_keys=True)
        self.path.write_text(text, encoding="utf-8")

    def _fetch(self, platform, slug, company):
        """-> список Job, либо None если такого борда нет (404)."""
        url, params, parser = PLATFORMS[platform]
        if platform == "teamtailor" and "." in slug:  # свой домен: careers.example.com
            url = "https://{slug}/jobs.rss"
        r = self.http.get(url.format(slug=slug), params=params)
        if r.status_code == 404 and platform == "lever":
            # европейские аккаунты Lever (jobs.eu.lever.co) живут на отдельном хосте
            r = self.http.get(f"https://api.eu.lever.co/v0/postings/{slug}", params=params)
        if r.status_code == 404:
            return None
        if r.status_code != 200:
            raise FetchError(f"{platform}/{slug}: HTTP {r.status_code}")
        try:
            data = r.text if platform == "teamtailor" else r.json()
            jobs = list(parser(data, company.name, slug) if platform == "bamboohr" else parser(data, company.name))
        except (ValueError, AttributeError, TypeError, ET.ParseError) as e:
            raise FetchError(f"{platform}/{slug}: не удалось разобрать ответ ({e})")
        for job in jobs:
            job.score, job.note = company.score, company.note
        if platform in ("bamboohr", "breezy"):
            self._fill_details(platform, slug, jobs)
        return jobs

    def _fill_details(self, platform, slug, jobs):
        """Текст вакансии для BambooHR/Breezy — только у подходящих по названию, не больше лимита."""
        from . import filters  # filters не импортирует ats, но держим импорт рядом с использованием

        candidates = [j for j in jobs if filters.title_candidate(j.title)][:DETAIL_FETCH_LIMIT]
        for job in candidates:
            try:
                if platform == "bamboohr":
                    r = self.http.get(f"https://{slug}.bamboohr.com/careers/{job.url.rsplit('/', 1)[-1]}/detail")
                    opening = (r.json().get("result") or {}).get("jobOpening") or {} if r.status_code == 200 else {}
                    job.description = html_to_text(opening.get("description"))
                    job.posted = job.posted or parse_date(opening.get("datePosted"))
                else:
                    r = self.http.get(job.url)
                    m = re.search(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', r.text, re.S) if r.status_code == 200 else None
                    posting = json.loads(m.group(1)) if m else {}
                    job.description = html_to_text(posting.get("description")) if isinstance(posting, dict) else ""
            except (FetchError, ValueError) as e:
                log.warning("%s: не удалось получить текст вакансии %s (%s)", job.company, job.url, e)

    def _discover(self, company):
        """Перебор platform × slug до первого борда с вакансиями.

        Пустой борд за находку не считаем: на Workable такие "пустышки" есть
        почти у любого имени, и они навсегда заслонили бы настоящий борд.
        """
        empty_hit, had_errors = None, False
        for slug in slug_candidates(company.name):
            for platform in DISCOVERABLE:
                try:
                    jobs = self._fetch(platform, slug, company)
                except FetchError as e:
                    had_errors = True
                    log.warning("%s: %s", company.name, e)
                    continue
                if jobs:
                    return platform, slug, jobs, had_errors
                if jobs is not None and empty_hit is None:
                    empty_hit = f"{platform}/{slug}"
        return None, empty_hit, [], had_errors

    def jobs_for(self, company, rediscover=False):
        with self.lock:
            entry = None if rediscover else self.mapping.get(company.name)
        override = geo.S.get("ats_overrides", {}).get(company.name, {})
        if override.get("greenhouse_board_token"):  # задан вручную в settings.toml
            entry = {"platform": "greenhouse", "slug": override["greenhouse_board_token"].strip()}
        today = date.today()
        if entry and entry.get("platform") == "custom":
            return []  # у компании свой сайт — её читает jobhunter/sites.py

        if entry and entry.get("platform"):
            try:
                jobs = self._fetch(entry["platform"], entry["slug"], company)
            except FetchError as e:
                log.warning("%s: %s", company.name, e)
                self.failures[company.name] = f"ошибка запроса: {e}"
                return []
            if jobs is None:
                self.failures[company.name] = f"борд {entry['platform']}/{entry['slug']} вернул 404"
                log.warning(
                    "%s: борд %s/%s больше не существует — поправьте ats_mapping.json "
                    "или запустите с --rediscover",
                    company.name, entry["platform"], entry["slug"],
                )
                return []
            return jobs

        if entry and entry.get("manual"):
            return []  # руками помечено «своя страница / проверять руками» — slug не подбираем
        checked = entry and (entry.get("checked_at") or entry.get("found_at"))
        if checked and (today - date.fromisoformat(checked)).days < config.RECHECK_NOT_FOUND_DAYS:
            return []

        platform, slug, jobs, had_errors = self._discover(company)
        if platform:
            log.info("%s -> %s/%s (%d вакансий)", company.name, platform, slug, len(jobs))
            new_entry = {"platform": platform, "slug": slug, "found_at": today.isoformat()}
        elif had_errors:
            return []  # сбой сети — не кэшируем "не найдено", попробуем завтра
        else:
            new_entry = {"platform": None, "checked_at": today.isoformat()}
            if slug:
                new_entry["empty_board"] = slug
            log.warning(
                "%s: slug не найден ни на одной ATS%s — пропущена",
                company.name, f" (только пустой борд {slug})" if slug else "",
            )
        with self.lock:
            self.mapping[company.name] = new_entry
        return jobs
