"""Вакансия для резюме по ссылке: из сохранённых отчётов, а если её там нет — со страницы вакансии.

Страница читается через jobhunter.http (robots.txt, 1 запрос в секунду на хост). Текст берётся из
разметки JobPosting (JSON-LD), которую отдают большинство ATS и карьерных сайтов, иначе — весь
текст страницы.
"""
from __future__ import annotations

import json
import re

from . import config
from .http import FetchError, Http
from .models import html_to_text
from .keys import norm_url

_JSONLD_RX = re.compile(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', re.S | re.I)
_TITLE_RX = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)


class VacancyNotFound(Exception):
    """Вакансию не удалось найти ни в отчётах, ни по ссылке."""


def archived(url: str, day: str = "") -> dict | None:
    """Вакансия из state/jobs/<дата>.json (сначала указанная дата, потом остальные от новых к старым)."""
    key = norm_url(url)
    files = sorted(config.JOBS_ARCHIVE_DIR.glob("*.json"), reverse=True)
    if day:
        files.sort(key=lambda p: p.stem != day)
    files.append(config.ROOT / "state" / "jobs_latest.json")
    for path in files:
        try:
            jobs = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for job in jobs:
            if job.get("url") and norm_url(job["url"]) == key:
                return {**job, "found_in": path.stem}
    return None


def _job_posting(raw: str) -> dict | None:
    """Первый JobPosting из JSON-LD страницы (в том числе внутри @graph и списков)."""
    for block in _JSONLD_RX.findall(raw):
        try:
            data = json.loads(block.strip())
        except ValueError:
            continue
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack += node
            elif isinstance(node, dict):
                kind = node.get("@type")
                if kind == "JobPosting" or (isinstance(kind, list) and "JobPosting" in kind):
                    return node
                stack += [v for v in node.values() if isinstance(v, (dict, list))]
    return None


def fetch(url: str, http: Http | None = None) -> dict:
    """Вакансия со страницы: {url, company, title, text}. Компания и название — из JobPosting или <title>.

    Raises:
        VacancyNotFound: страница недоступна, закрыта robots.txt или текста слишком мало.
    """
    http = http or Http()
    try:
        r = http.get(url, timeout=25, headers={"Accept": "text/html"})
    except FetchError as e:
        raise VacancyNotFound(f"страница не открылась: {e}") from e
    if r.status_code != 200:
        raise VacancyNotFound(f"страница ответила HTTP {r.status_code}")
    posting = _job_posting(r.text)
    if posting:
        org = posting.get("hiringOrganization") or {}
        company = org.get("name", "") if isinstance(org, dict) else str(org)
        text = html_to_text(posting.get("description", ""))
        title = posting.get("title", "")
    else:
        visible = re.sub(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", r.text)
        company, text = "", html_to_text(visible)
        title = html_to_text((_TITLE_RX.search(r.text) or [None, ""])[1])
    if len(text) < 300:  # список вакансий, страница входа или вакансия рисуется скриптом
        raise VacancyNotFound("на странице почти нет текста вакансии — вставьте текст вручную")
    return {"url": url, "company": company.strip(), "title": title.strip(), "text": text, "found_in": "page"}
