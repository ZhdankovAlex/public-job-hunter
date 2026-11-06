"""Зарплаты по стране вакансии с levels.fyi: медиана и 25–75 перцентили Software Engineer.

Источник — Markdown-версии страниц, которые levels.fyi отдаёт агентам
(https://www.levels.fyi/t/software-engineer/locations/<страна>.md, см. их robots.txt и llms.txt):
данные можно читать и цитировать со ссылкой, обучать на них модели нельзя. Читаем через
jobhunter.http (robots.txt, 1 запрос/с), кэшируем на CACHE_DAYS — за прогон это десяток запросов.

Цифры — total compensation (оклад + бонусы + акции) по всем грейдам, присланные людьми;
senior обычно ближе к 75-му перцентилю. Пересчёт в EUR — по приблизительным курсам из
salary.rates() (settings.toml → [salary].rates_to_eur).
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date

from . import config, progress, salary
from .http import FetchError, Http

log = logging.getLogger(__name__)

CACHE_FILE = config.ROOT / "state" / "levels_cache.json"
CACHE_DAYS = 7
BASE_URL = "https://www.levels.fyi/t/software-engineer/locations/{slug}"
EUROPE = "europe"

# страница levels.fyi -> (как подписать, признаки в локации вакансии: страна, города, коды)
LOCATIONS = {
    "germany": ("Германия", r"germany|deutschland|berlin|munich|münchen|hamburg|frankfurt|cologne|köln|\bDE\b"),
    "netherlands": ("Нидерланды", r"netherlands|amsterdam|rotterdam|utrecht|eindhoven|the hague|\bNL\b"),
    "cyprus": ("Кипр", r"cyprus|limassol|nicosia|larnaca|paphos"),
    "portugal": ("Португалия", r"portugal|lisbon|lisboa|porto\b"),
    "spain": ("Испания", r"spain|españa|madrid|barcelona|valencia|malaga|málaga"),
    "poland": ("Польша", r"poland|warsaw|warszawa|krakow|kraków|wroclaw|wrocław|gdansk|gdańsk|\bPL\b"),
    "united-kingdom": ("Великобритания", r"united kingdom|\bUK\b|london|manchester|edinburgh|england"),
    "ireland": ("Ирландия", r"ireland|dublin"),
    "france": ("Франция", r"france|paris|lyon"),
    "switzerland": ("Швейцария", r"switzerland|zurich|zürich|geneva|basel"),
    "austria": ("Австрия", r"austria|vienna|wien"),
    "czech-republic": ("Чехия", r"czech|prague|praha|brno"),
    "estonia": ("Эстония", r"estonia|tallinn"),
    "lithuania": ("Литва", r"lithuania|vilnius"),
    "latvia": ("Латвия", r"latvia|riga\b"),
    "greece": ("Греция", r"greece|athens"),
    "serbia": ("Сербия", r"serbia|belgrade|beograd|novi sad|\bRS\b"),
    "georgia": ("Грузия", r"tbilisi|batumi|georgia \(country\)|sakartvelo"),
    "armenia": ("Армения", r"armenia|yerevan"),
    "united-arab-emirates": ("ОАЭ", r"united arab emirates|\bUAE\b|dubai|abu dhabi"),
    "israel": ("Израиль", r"israel|tel aviv"),
    "united-states": ("США", r"united states|\bUSA?\b|new york|san francisco|seattle|austin|boston|chicago"),
    "canada": ("Канада", r"canada|toronto|vancouver|montreal|montréal"),
}
_LOCATION_RX = {slug: re.compile(rx, re.I) for slug, (_, rx) in LOCATIONS.items()}
# remote без страны: ориентир — домашняя страна (settings.toml [home]), откуда работает кандидат;
# если её нет в LOCATIONS — Европа целиком
REMOTE_DEFAULT = next((slug for slug, rx in _LOCATION_RX.items() if config.HOME_COUNTRY and rx.search(config.HOME_COUNTRY)),
                      EUROPE)
_REMOTE_LABEL = f"{LOCATIONS[REMOTE_DEFAULT][0]} (remote)" if REMOTE_DEFAULT in LOCATIONS else "Европа (remote)"
_EUROPE_RX = re.compile(r"europe|\bEU\b|\bEMEA\b|\bCET\b", re.I)
_REMOTE_RX = re.compile(r"remote|worldwide|anywhere|global|удал", re.I)

# валюты, которых нет в salary.DEFAULT_RATES (приблизительно, EUR за единицу)
EXTRA_RATES = {"AED": 0.25, "CZK": 0.04, "RSD": 0.0085, "ILS": 0.25, "SEK": 0.087, "DKK": 0.134,
               "NOK": 0.085, "HUF": 0.0025, "GEL": 0.34, "AMD": 0.0024, "BGN": 0.51, "RON": 0.2}


def location_slug(location: str, reloc_country: str = "") -> tuple[str, str]:
    """(страница levels.fyi, пояснение) для локации вакансии; ("", "") — не определить.

    Страна / город в локации — эта страна; «Remote Europe / EU / CET» — Европа целиком;
    просто remote / worldwide — домашняя страна (REMOTE_DEFAULT): удалённо платят с оглядкой на страну работника.
    """
    text = f"{location} {reloc_country}"
    for slug, rx in _LOCATION_RX.items():
        if rx.search(text):
            return slug, LOCATIONS[slug][0]
    if _EUROPE_RX.search(text):
        return EUROPE, "Европа"
    if _REMOTE_RX.search(text) or not location.strip():
        return REMOTE_DEFAULT, _REMOTE_LABEL
    return "", ""


_NUMBER_RX = re.compile(r"([\d,.]+)\s*([KM])?", re.I)


def _amount(text: str) -> float | None:
    """«€71,199» / «CZK 1,026,759 per year» / «$62.8K» -> число."""
    m = _NUMBER_RX.search(text.replace("\xa0", " "))
    if not m:
        return None
    value = float(m.group(1).replace(",", ""))
    return value * {"K": 1e3, "M": 1e6}.get((m.group(2) or "").upper(), 1)


def parse(md: str) -> dict | None:
    """Из Markdown-страницы levels.fyi: валюта, медиана / перцентили за год, число зарплат."""
    currency = re.search(r"\*\*Currency:\*\*\s*([A-Z]{3})", md)
    median = re.search(r"Median Total Compensation:\s*([^\n]+)", md)
    quartiles = re.search(r"25th / 75th Percentile:\s*([^/\n]+)/([^\n]+)", md)
    sample = re.search(r"Sample Size:\s*([\d,]+)", md)
    if not (currency and median and quartiles and sample):
        return None
    median_value = _amount(median.group(1))
    # у части валют медиана дана в месяц, а в скобках — «(… per year)»
    if median_value is not None and "per year)" in median.group(1):
        median_value *= 12
    return {
        "currency": currency.group(1), "median": median_value,
        "p25": _amount(quartiles.group(1)), "p75": _amount(quartiles.group(2)),
        "samples": int(sample.group(1).replace(",", "")),
    }


class Levels:
    """Кэш страниц levels.fyi по странам и строка для отчёта."""

    def __init__(self, http: Http | None = None):
        self.http = http
        try:
            self.cache: dict = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.cache = {}
        self.rates = {**EXTRA_RATES, **salary.rates()}

    def get(self, slug: str) -> dict | None:
        """Данные страницы: из кэша, если моложе CACHE_DAYS, иначе запрос (при сбое — старый кэш)."""
        entry = self.cache.get(slug)
        fresh = entry and (date.today() - date.fromisoformat(entry["fetched"])).days < CACHE_DAYS
        if fresh or self.http is None:
            return entry
        url = BASE_URL.format(slug=slug)
        try:
            progress.step(f"levels.fyi: {slug}")
            r = self.http.get(url + ".md")
            data = parse(r.text) if r.status_code == 200 else None
        except FetchError as e:
            log.warning("levels.fyi %s: %s — беру кэш", slug, e)
            return entry
        if data is None:
            log.warning("levels.fyi %s: страница не разобралась (HTTP %s) — беру кэш", slug, r.status_code)
            return entry
        self.cache[slug] = {**data, "url": url, "fetched": date.today().isoformat()}
        return self.cache[slug]

    def save(self) -> None:
        CACHE_FILE.write_text(json.dumps(self.cache, ensure_ascii=False, indent=1), encoding="utf-8")

    def _eur_month(self, value: float | None, currency: str) -> float | None:
        rate = self.rates.get(currency)
        return None if value is None or rate is None else value * rate / 12

    def describe(self, location: str, reloc_country: str = "") -> tuple[str, str]:
        """(строка, ссылка): «levels.fyi, Кипр: медиана ≈ €5.9K/мес, 25–75%: €4.6–7.2K (137 зарплат)»."""
        slug, label = location_slug(location, reloc_country)
        data = self.get(slug) if slug else None
        if not data:
            return "", ""
        median, low, high = (self._eur_month(data[k], data["currency"]) for k in ("median", "p25", "p75"))
        if median is None:
            return f"levels.fyi, {label}: {data['currency']} {data['median']:,.0f}/год", data["url"]
        k = lambda v: f"{v / 1000:.1f}K"  # noqa: E731
        spread = f", 25–75%: €{k(low)}–{k(high)}" if low and high else ""
        return f"levels.fyi, {label}: медиана ≈ €{k(median)}/мес{spread} ({data['samples']} зарплат)", data["url"]
