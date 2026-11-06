"""Отклики из applications_manual.toml: на какие вакансии и в какие компании уже откликались."""
import logging
import re
import tomllib

from . import config
from .keys import norm_url, url_ids
from .models import norm_name

log = logging.getLogger(__name__)

_URL_RX = re.compile(r"https?://\S+")


class Applications:
    def __init__(self, rows):
        header = [h.strip().lower() for h in rows[0]] if rows else []

        def col(name, default):
            return next((i for i, h in enumerate(header) if h.startswith(name)), default)

        ci, si, ui = col("компания", 0), col("статус", 1), col("вакансия", 2)
        self.urls, self.ids, self.companies = {}, {}, {}  # ссылка / id вакансии -> статус
        self.count = 0
        for row in rows[1:]:
            row = row + [""] * (max(ci, si, ui) + 1 - len(row))
            company, status = row[ci].strip(), row[si].strip()
            if not company and not row[ui].strip():
                continue
            self.count += 1
            for url in _URL_RX.findall(row[ui]):
                self.urls[norm_url(url)] = status
                self.ids.update(dict.fromkeys(url_ids(url), status))
            if company:
                self.companies.setdefault(norm_name(company), set()).add(status or "без статуса")

    def job_status(self, job):
        """Статус отклика на эту вакансию, None — откликов на неё нет."""
        status = self.urls.get(norm_url(job.url))
        if status is None:
            status = next((self.ids[i] for i in url_ids(job.url) if i in self.ids), None)
        return status

    def has_job(self, job):
        """Вакансия уже в работе: отклик без ответа или идёт собеседование.

        После «Отказ» / «Игнор» / «Отказался сам» вакансия снова показывается — можно откликнуться ещё раз.
        """
        status = self.job_status(job)
        return status is not None and not config.RETRY_STATUS_RX.search(status)

    def company_statuses(self, company):
        key = norm_name(company)
        for known, statuses in self.companies.items():
            short, long_ = sorted((key, known), key=len)
            if short == long_ or (len(short) >= 3 and long_.startswith(short)):
                return ", ".join(sorted(statuses))
        return ""


# kind события из applications_manual.toml -> статус (RETRY_STATUS_RX понимает «Отказ»)
_KIND_STATUS = {"applied": "Отклик", "interview": "Собеседование", "rejected": "Отказ", "offer": "Оффер",
                "declined": "Отказался сам", "other": "Отклик"}


def _manual_rows() -> list[list[str]]:
    """Строки «компания, статус, ссылка» из событий applications_manual.toml, где указан url."""
    if not config.MANUAL_APPLICATIONS_FILE.exists():
        return []
    data = tomllib.loads(config.MANUAL_APPLICATIONS_FILE.read_text(encoding="utf-8"))
    rows = []
    for entry in data.get("application", []):
        for event in entry.get("events", []):
            if event.get("url"):
                rows.append([entry["company"], _KIND_STATUS.get(event.get("kind", ""), "Отклик"), event["url"]])
    return rows


def load_applications():
    """Отклики из событий с url в applications_manual.toml -> Applications."""
    apps = Applications([["Компания", "Статус", "Вакансия"], *_manual_rows()])
    log.info("Отклики: %d строк (applications_manual.toml)", apps.count)
    return apps
