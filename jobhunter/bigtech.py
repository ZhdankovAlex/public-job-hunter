"""Карьерные сайты крупных компаний: Microsoft, Workday-сайты (NVIDIA, Salesforce…), Oracle, Amazon.

У всех открытый JSON-поиск, которым пользуется сам сайт; robots.txt проверяет Http, лимит —
1 запрос в секунду на хост. Поиск по словам из settings.toml [bigtech].queries; полный текст
открывается только для вакансий, прошедших фильтр по названию, не больше details_per_company
на компанию — сначала домашняя страна ([home]) и remote, потом самые свежие (релокацию видно только по тексту).
"""
from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

from . import config as c
from . import filters, geo
from .http import FetchError, Http
from .models import Job, html_to_text, parse_date

log = logging.getLogger(__name__)

SETTINGS = geo.S.get("bigtech", {})
QUERIES = SETTINGS.get("queries", ["python", "backend"])
PAGES = int(SETTINGS.get("pages", 3))
DETAILS_PER_COMPANY = int(SETTINGS.get("details_per_company", 60))


def _priority(location: str, posted) -> tuple:
    """Порядок открытия вакансий: домашняя страна, remote, Европа, остальное; внутри — свежие первыми."""
    if c.HOME_RX.search(location):
        rank = 0
    elif c.REMOTE_RX.search(location):
        rank = 1
    elif c.LOCATION_EUROPE_RX.search(location):
        rank = 2
    else:
        rank = 3
    return rank, -(posted.timestamp() if posted else 0)


def _pick(cards: dict) -> list:
    """Карточки, которые стоит открыть: подходящее название, не больше DETAILS_PER_COMPANY."""
    good = [card for card in cards.values() if filters.title_candidate(card["title"])]
    good.sort(key=lambda card: _priority(card["location"], card["posted"]))
    return good[:DETAILS_PER_COMPANY]


# --- Microsoft: apply.careers.microsoft.com (Eightfold, /api/pcsx разрешён в robots.txt) ---

_MS = "https://apply.careers.microsoft.com"


def microsoft(http: Http) -> Iterator[Job]:
    cards = {}
    searches = [(q, "") for q in QUERIES] + ([("", c.HOME_COUNTRY)] if c.HOME_COUNTRY else [])
    for query, location in searches:
        for page in range(PAGES):
            r = http.get(f"{_MS}/api/pcsx/search", params={
                "domain": "microsoft.com", "query": query, "location": location,
                "start": page * 10, "num": 10, "sort_by": "relevance",
            })
            if r.status_code != 200:
                raise FetchError(f"microsoft: HTTP {r.status_code}")
            positions = (r.json().get("data") or {}).get("positions") or []
            for p in positions:
                cards.setdefault(p["id"], {
                    "id": p["id"], "title": p.get("name") or "",
                    "location": "; ".join(p.get("locations") or []),
                    "posted": parse_date(p.get("postedTs")),
                    "remote": p.get("workLocationOption") == "remote",
                })
            if len(positions) < 10:
                break
    for card in _pick(cards):
        try:
            r = http.get(f"{_MS}/api/pcsx/position_details",
                         params={"position_id": card["id"], "domain": "microsoft.com", "hl": "en"})
            data = r.json().get("data") or {}
        except (FetchError, ValueError) as e:
            log.warning("microsoft %s: %s", card["id"], e)
            continue
        yield Job(
            company="Microsoft", title=card["title"], location=card["location"],
            url=data.get("publicUrl") or f"{_MS}/careers/job/{card['id']}", posted=card["posted"],
            description=html_to_text(data.get("jobDescription") or ""), remote=card["remote"],
            employment=data.get("efcustomTextEmploymentType") or "", source="microsoft",
        )


# --- Workday: {tenant}.wdN.myworkdayjobs.com, поиск — POST /wday/cxs/{tenant}/{site}/jobs ---

_POSTED_RX = re.compile(r"posted\s+(today|yesterday|(\d+)\+?\s+days?\s+ago)", re.I)


def _workday_posted(text: str):
    """'Posted 3 Days Ago' / 'Posted Today' / 'Posted 30+ Days Ago' -> datetime (UTC)."""
    m = _POSTED_RX.search(text or "")
    if not m:
        return None
    days = 0 if m.group(1).lower() == "today" else 1 if m.group(1).lower() == "yesterday" else int(m.group(2))
    return datetime.now(timezone.utc) - timedelta(days=days)


def workday(company: str, host: str, tenant: str, site: str):
    """Источник для одного Workday-сайта."""
    base = f"https://{host}.myworkdayjobs.com"
    api = f"{base}/wday/cxs/{tenant}/{site}"

    def fetch(http: Http) -> Iterator[Job]:
        cards = {}
        for query in QUERIES:
            for page in range(PAGES):
                r = http.post(f"{api}/jobs", json={
                    "appliedFacets": {}, "limit": 20, "offset": page * 20, "searchText": query,
                })
                if r.status_code != 200:
                    raise FetchError(f"workday {company}: HTTP {r.status_code}")
                postings = r.json().get("jobPostings") or []
                for p in postings:
                    if p.get("externalPath"):
                        cards.setdefault(p["externalPath"], {
                            "path": p["externalPath"], "title": p.get("title") or "",
                            "location": p.get("locationsText") or "", "posted": _workday_posted(p.get("postedOn")),
                        })
                if len(postings) < 20:
                    break
        for card in _pick(cards):
            try:
                info = http.get(f"{api}{card['path']}").json().get("jobPostingInfo") or {}
            except (FetchError, ValueError) as e:
                log.warning("workday %s %s: %s", company, card["path"], e)
                continue
            places = [info.get("location") or card["location"], *(info.get("additionalLocations") or [])]
            location = "; ".join(p for p in places if p)
            yield Job(
                company=company, title=info.get("title") or card["title"], location=location,
                url=info.get("externalUrl") or f"{base}/{site}{card['path']}",
                posted=parse_date(info.get("startDate")) or card["posted"],
                description=html_to_text(info.get("jobDescription") or ""),
                remote=bool(c.REMOTE_RX.search(f"{location} {info.get('remoteType') or ''}")) or None,
                employment=info.get("timeType") or "", source="workday",
            )

    return fetch


# --- Oracle: Oracle Recruiting Cloud (hcmRestApi), сайт CX_45001 ------------------------

_ORACLE = "https://eeho.fa.us2.oraclecloud.com/hcmRestApi/resources/latest"
_ORACLE_SITE = "CX_45001"


def oracle(http: Http) -> Iterator[Job]:
    cards = {}
    for query in QUERIES:
        for page in range(PAGES):
            r = http.get(f"{_ORACLE}/recruitingCEJobRequisitions", params={
                "onlyData": "true", "expand": "requisitionList.secondaryLocations",
                "finder": f'findReqs;siteNumber={_ORACLE_SITE},keyword="{query}",limit=25,'
                          f"offset={page * 25},sortBy=POSTING_DATES_DESC",
            })
            if r.status_code != 200:
                raise FetchError(f"oracle: HTTP {r.status_code}")
            items = r.json().get("items") or [{}]
            reqs = items[0].get("requisitionList") or []
            for q in reqs:
                places = [q.get("PrimaryLocation") or "",
                          *(x.get("Name") or "" for x in q.get("secondaryLocations") or [])]
                cards.setdefault(q["Id"], {
                    "id": q["Id"], "title": q.get("Title") or "", "location": "; ".join(p for p in places if p),
                    "posted": parse_date(q.get("PostedDate")), "workplace": q.get("WorkplaceType") or "",
                })
            if len(reqs) < 25:
                break
    for card in _pick(cards):
        try:
            r = http.get(f"{_ORACLE}/recruitingCEJobRequisitionDetails", params={
                "expand": "all", "onlyData": "true",
                "finder": f'ById;Id="{card["id"]}",siteNumber={_ORACLE_SITE}',
            })
            item = (r.json().get("items") or [{}])[0]
        except (FetchError, ValueError) as e:
            log.warning("oracle %s: %s", card["id"], e)
            continue
        text = "\n".join(html_to_text(item.get(k) or "") for k in (
            "ExternalDescriptionStr", "ExternalResponsibilitiesStr", "ExternalQualificationsStr"))
        workplace = item.get("WorkplaceType") or card["workplace"]
        yield Job(
            company="Oracle", title=card["title"], location=card["location"],
            url=f"https://careers.oracle.com/jobs/#en/sites/jobsearch/job/{card['id']}", posted=card["posted"],
            description=text, remote=bool(re.search(r"remote", workplace, re.I)) or None,
            employment=item.get("JobSchedule") or "", source="oracle",
        )


# --- Amazon: amazon.jobs/en/search.json — полный текст сразу в ответе поиска ---------------

def amazon(http: Http) -> Iterator[Job]:
    seen = set()
    for query in QUERIES:
        for page in range(PAGES):
            r = http.get("https://www.amazon.jobs/en/search.json", params={
                "base_query": query, "result_limit": 100, "offset": page * 100, "sort": "recent",
            })
            if r.status_code != 200:
                raise FetchError(f"amazon: HTTP {r.status_code}")
            jobs = r.json().get("jobs") or []
            for j in jobs:
                if j.get("id_icims") in seen or not filters.title_candidate(j.get("title") or ""):
                    continue
                seen.add(j.get("id_icims"))
                try:
                    posted = datetime.strptime(j.get("posted_date") or "", "%B %d, %Y").replace(tzinfo=timezone.utc)
                except ValueError:
                    posted = None
                location = j.get("normalized_location") or j.get("location") or ""
                yield Job(
                    company="Amazon", title=j.get("title") or "", location=location,
                    url=f"https://www.amazon.jobs{j.get('job_path') or ''}", posted=posted,
                    description="\n".join(html_to_text(j.get(k) or "") for k in (
                        "description", "basic_qualifications", "preferred_qualifications")),
                    remote=bool(c.REMOTE_RX.search(location)) or None,
                    employment=j.get("job_schedule_type") or "", source="amazon",
                )
            if len(jobs) < 100:
                break


SOURCES = {"microsoft": microsoft, "oracle": oracle, "amazon": amazon}
for _company, _host, _tenant, _site in (
    tuple(x) for x in SETTINGS.get("workday", [])
):
    SOURCES[f"workday:{_company}"] = workday(_company, _host, _tenant, _site)
