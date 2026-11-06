"""Открытые агрегаторы с публичным API: Arbeitnow, Remote OK, Himalayas."""
import logging

from . import config
from .http import FetchError
from .models import Job, html_to_text, parse_date

log = logging.getLogger(__name__)


def _json(http, url, params=None):
    r = http.get(url, params=params)
    if r.status_code != 200:
        raise FetchError(f"{url}: HTTP {r.status_code}")
    try:
        return r.json()
    except ValueError as e:
        raise FetchError(f"{url}: не JSON ({e})")


def arbeitnow(http):
    for page in range(1, config.ARBEITNOW_PAGES + 1):
        data = _json(http, "https://www.arbeitnow.com/api/job-board-api", {"page": page})
        for j in data.get("data", []):
            yield Job(
                company=j.get("company_name", ""),
                title=j.get("title", ""),
                location=j.get("location") or "",
                url=j.get("url", ""),
                posted=parse_date(j.get("created_at")),
                description=html_to_text(j.get("description")) + "\n" + ", ".join(j.get("tags") or []),
                remote=bool(j.get("remote")),
                employment=", ".join(j.get("job_types") or []),
                source="arbeitnow",
            )
        if not (data.get("links") or {}).get("next"):
            break


def remoteok(http):
    data = _json(http, "https://remoteok.com/api")
    for j in data:
        if not isinstance(j, dict) or "position" not in j:
            continue  # первый элемент — юридическая заметка
        yield Job(
            company=j.get("company", ""),
            title=j.get("position", ""),
            location=j.get("location") or "",
            url=j.get("url", ""),
            posted=parse_date(j.get("date") or j.get("epoch")),
            description=html_to_text(j.get("description")) + "\n" + ", ".join(j.get("tags") or []),
            remote=True,
            source="remoteok",
        )


def _himalayas_salary(j):
    low, high = j.get("minSalary"), j.get("maxSalary")
    if not low and not high:
        return ""
    amount = f"{low:,}–{high:,}" if low and high and low != high else f"{low or high:,}"
    return f"{j.get('currency') or ''} {amount} / {j.get('salaryPeriod') or 'year'}".strip()


def himalayas(http):
    for params in config.HIMALAYAS_QUERIES:
        data = _json(http, "https://himalayas.app/jobs/api/search", {**params, "sort": "recent"})
        for j in data.get("jobs", []):
            restrictions = j.get("locationRestrictions") or []
            yield Job(
                company=j.get("companyName", ""),
                title=j.get("title", ""),
                location=", ".join(restrictions) if restrictions else "Worldwide",
                url=j.get("applicationLink") or j.get("guid") or "",
                posted=parse_date(j.get("pubDate")),
                description=html_to_text(j.get("description")),
                remote=True,
                source="himalayas",
                salary=_himalayas_salary(j),
                employment=j.get("employmentType") or "",
            )


SOURCES = {"arbeitnow": arbeitnow, "remoteok": remoteok, "himalayas": himalayas}


def fetch(name, http):
    """Собирает что успеет; при ошибке возвращает уже полученное."""
    jobs = []
    try:
        for job in SOURCES[name](http):
            jobs.append(job)
    except FetchError as e:
        log.warning("агрегатор %s: %s", name, e)
    return jobs
