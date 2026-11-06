"""Зарплатные вилки: разбор указанных в вакансиях сумм и оценка для вакансий без вилки.

Оценка строится по накопленной базе вакансий с явно указанной вилкой
(state/salary_samples.json): Telegram-каналы (youritjob, IT_iGaming, af_job_board и др.)
и доски/агрегаторы, где зарплата приходит отдельным полем. Всё приводится к EUR в месяц.
Это ориентир по рынку, а не предложение конкретной компании.
"""
from __future__ import annotations

import json
import re
import statistics
from datetime import date, timedelta

from . import config

SAMPLES_FILE = config.ROOT / "state" / "salary_samples.json"
KEEP_DAYS = 180
MIN_SAMPLES = 4
MIN_SAMPLES_CIS = 3  # вилок из каналов русскоязычного рынка мало
MARKETS = (("cis", "каналы русскоязычного рынка"), ("global", "глобальный рынок, в основном США и ЕС"))
DEFAULT_RATES = {"EUR": 1.0, "USD": 0.92, "GBP": 1.17, "PLN": 0.23, "CHF": 1.05, "CAD": 0.67, "AUD": 0.6, "RUB": 0.0095}

_CURRENCY_RX = re.compile(r"(\$|€|£|₽|\busd\b|\beur\b|\bgbp\b|\bpln\b|zł|\bchf\b|\bcad\b|\baud\b|\brub\b|руб)", re.I)
_CURRENCY = {"$": "USD", "usd": "USD", "€": "EUR", "eur": "EUR", "£": "GBP", "gbp": "GBP", "pln": "PLN",
             "zł": "PLN", "chf": "CHF", "cad": "CAD", "aud": "AUD", "₽": "RUB", "rub": "RUB", "руб": "RUB"}
_NUMBER_RX = re.compile(r"(\d{1,3}(?:[  .,]\d{3})+|\d+(?:[.,]\d+)?)\s?([kкKК])?(?![\w%])")
_YEAR_RX = re.compile(r"year|annual|annum|/\s?yr|\bp\.?a\.?\b|в год|год", re.I)
_HOUR_RX = re.compile(r"hour|/\s?hr?\b|/h\b|в час|час", re.I)
_DAY_RX = re.compile(r"\bday\b|/\s?day|в день", re.I)

# Семейства ролей — порядок важен: сначала более узкие
_NOT_ENGINEERING_RX = re.compile(
    r"marketing|design(?:er)? engineer|advocate|solutions?|forward deployed|sales|support|customer|"
    r"growth engineer|founding|autonomy|system engineer|hardware|mechanical|electrical", re.I)

_FAMILIES = (
    ("management", re.compile(r"manager|director|\bhead\b|product owner|\bcto\b|\bcpo\b|менеджер|руководител", re.I)),
    ("qa", re.compile(r"\bqa\b|\btest|quality|тестировщик|sdet", re.I)),
    ("frontend", re.compile(r"front[\s-]?end|react|vue|angular|фронт", re.I)),
    ("mobile", re.compile(r"\bios\b|android|mobile|flutter", re.I)),
    ("devops", re.compile(r"devops|\bsre\b|site reliability|infrastructure|platform|dataops|cloud|sysadmin|системн", re.I)),
    ("data", re.compile(r"data (?:engineer|analyst|scientist)|analytics|analyst|аналитик", re.I)),
    ("ml", re.compile(r"\bml\b|\bai\b|machine learning|\bllm\b|deep learning", re.I)),
    ("fullstack", re.compile(r"full[\s-]?stack|фулстек", re.I)),
    ("backend", re.compile(
        r"back[\s-]?end|б[эе]к[\s-]?енд|python|django|java\b|kotlin|golang|\bgo\b|node|php|\.net|ruby|scala|"
        r"software (?:development )?(?:engineer|developer)|разработчик|программист", re.I)),
)
FAMILY_NAMES = {"backend": "backend", "fullstack": "fullstack", "ml": "ML/AI", "data": "data", "devops": "DevOps/платформа",
                "mobile": "mobile", "frontend": "frontend", "qa": "QA", "management": "менеджмент"}
_SENIORITY = (
    ("lead", re.compile(r"\blead\b|staff|principal|architect|тимлид|\bhead\b", re.I)),
    ("senior", re.compile(r"senior|\bsr\.?\b|сеньор|старш", re.I)),
    ("middle", re.compile(r"middle|\bmid\b|мидл", re.I)),
    ("junior", re.compile(r"junior|\bjr\.?\b|intern|trainee|джун|стаж", re.I)),
)


def rates() -> dict[str, float]:
    """Курсы к EUR: из settings.toml ([salary.rates_to_eur]) поверх значений по умолчанию."""
    from . import geo

    return {**DEFAULT_RATES, **(geo.S.get("salary", {}).get("rates_to_eur") or {})}


def min_monthly() -> int:
    """Порог по деньгам, EUR/мес ([salary] min_monthly_eur в settings.toml; 0 — выключен)."""
    from . import geo

    return int(geo.S.get("salary", {}).get("min_monthly_eur") or 0)


def below_min(job) -> bool:
    """Вилка, указанная в самой вакансии, целиком ниже порога (верхняя граница < min_monthly_eur).

    Только явная вилка (parse): оценка по базе и levels.fyi не считаются — без вилки вакансия не «дешёвая».
    """
    limit = min_monthly()
    stated = parse(getattr(job, "salary", "") or "") if limit else None
    return bool(stated) and stated[1] < limit


def meets_min(job) -> bool:
    """В вакансии указана вилка, и её верхняя граница не ниже min_monthly_eur. Без вилки — False."""
    limit = min_monthly()
    stated = parse(getattr(job, "salary", "") or "") if limit else None
    return bool(stated) and stated[1] >= limit


def family(title: str) -> str:
    if _NOT_ENGINEERING_RX.search(title):
        return ""
    return next((name for name, rx in _FAMILIES if rx.search(title)), "")


MONTHLY_BIG_CURRENCIES = {"RUB", "KZT"}

# требуемый опыт: «Опыт работы: 1–3 года», «3–6 лет», «более 6 лет», «от 5 лет», «5+ years»
_EXPERIENCE_RANGE_RX = re.compile(
    r"опыт\w*[^.\n]{0,40}?(?:от\s*)?(\d{1,2})\s*(?:[–—-]\s*(\d{1,2}))?\s*(?:\+\s*)?(?:года?|лет)|"
    r"(?:более|свыше)\s+(\d{1,2})\s+лет|"
    r"(\d{1,2})\s*(?:[–—-]\s*\d{1,2}\s*)?\+?\s*(?:years?|yrs)(?:['’]?\s+of)?\s+(?:[\w-]+\s+){0,3}experience",
    re.I,
)
GRADE_NAMES = {"junior": "Junior", "middle": "Middle", "senior": "Senior", "lead": "Lead / Staff"}


def seniority(title: str) -> str:
    return next((name for name, rx in _SENIORITY if rx.search(title)), "")


def required_years(text: str) -> int | None:
    """Минимальный требуемый опыт в годах (первое упоминание) или None."""
    m = _EXPERIENCE_RANGE_RX.search(text or "")
    if not m:
        return None
    value = next((g for g in m.groups() if g), None)
    return int(value) if value else None


def grade(title: str, text: str = "") -> str:
    """Грейд вакансии: по названию, а если там его нет — по требуемому опыту.

    0–1 год — junior, 2–3 — middle, 4–7 — senior, от 8 — lead.
    """
    level = seniority(title)
    if level:
        return level
    years = required_years(text)
    if years is None:
        return ""
    return "junior" if years <= 1 else "middle" if years <= 3 else "senior" if years <= 7 else "lead"


def _number(raw: str, k: str | None) -> float:
    raw = raw.replace(" ", " ")
    if k:
        return float(raw.replace(" ", "").replace(",", ".")) * 1000
    if re.fullmatch(r"\d{1,3}(?:[ .,]\d{3})+", raw):
        return float(re.sub(r"[ .,]", "", raw))
    return float(raw.replace(",", "."))


def parse(text: str) -> tuple[int, int] | None:
    """Вилка из строки с зарплатой -> (от, до) в EUR за месяц; None, если разобрать нельзя."""
    if not text:
        return None
    currency = _CURRENCY_RX.search(text)
    if not currency or _HOUR_RX.search(text) or _DAY_RX.search(text):
        return None
    rate = rates().get(_CURRENCY[currency.group(1).lower()], 0)
    numbers = [_number(n, k) for n, k in _NUMBER_RX.findall(text)]
    numbers = [n for n in numbers if n >= 300]
    if not numbers or not rate:
        return None
    low, high = numbers[0], (numbers[1] if len(numbers) > 1 else numbers[0])
    if high < low:
        high = low
    code = _CURRENCY[currency.group(1).lower()]
    # в рублях и тенге месячная зарплата больше 25 000 — обычное дело; год только по явному «в год / annual»
    yearly = bool(_YEAR_RX.search(text)) or (low >= 25000 and code not in MONTHLY_BIG_CURRENCIES)
    divisor = 12 if yearly else 1
    low, high = low * rate / divisor, high * rate / divisor
    if not 300 <= low <= 40000 or not 300 <= high <= 60000:
        return None
    return round(low), round(high)


def _fmt(low: int, high: int) -> str:
    def k(v):
        return f"{round(v / 100) * 100:,}".replace(",", " ")
    return f"€{k(low)}" if abs(high - low) < 100 else f"€{k(low)}–{k(high)}"


class SalaryBase:
    """Накопленные вилки и оценка по ним."""

    def __init__(self) -> None:
        """Читает базу с диска и выбрасывает записи старше KEEP_DAYS."""
        self.samples: dict[str, dict] = {}
        if SAMPLES_FILE.exists():
            self.samples = json.loads(SAMPLES_FILE.read_text(encoding="utf-8"))
        cutoff = (date.today() - timedelta(days=KEEP_DAYS)).isoformat()
        self.samples = {k: v for k, v in self.samples.items() if v.get("date", "") >= cutoff}

    def add(self, key: str, title: str, salary_text: str, market: str, source: str, day: str) -> None:
        """Добавляет вакансию с указанной вилкой.

        market: 'cis' — каналы русскоязычного рынка (вилки обычно в месяц, «на руки»);
        'global' — международные подборки и доски (вилки чаще годовые, США/ЕС).
        """
        parsed = parse(salary_text)
        fam = family(title)
        if not parsed or not fam or not key:
            return
        self.samples[key] = {
            "family": fam, "seniority": seniority(title), "low": parsed[0], "high": parsed[1],
            "market": market, "source": source, "date": day, "title": title[:90],
        }

    def save(self) -> None:
        SAMPLES_FILE.parent.mkdir(exist_ok=True)
        SAMPLES_FILE.write_text(json.dumps(self.samples, ensure_ascii=False, indent=0), encoding="utf-8")

    def _pool(self, fam: str, level: str, market: str) -> list[dict]:
        return [s for s in self.samples.values()
                if s["family"] == fam and s["market"] == market and (not level or s["seniority"] == level)]

    def describe(self, title: str, salary_text: str = "", level: str = "") -> str:
        """Строка для отчёта: указанная вилка в EUR/мес либо оценка по базе ('' — данных мало).

        level — грейд вакансии (salary.grade: название или требуемый опыт).
        """
        stated = parse(salary_text)
        if stated:
            return f"{_fmt(*stated)}/мес (указана)"
        fam, level = family(title), level or seniority(title)
        if not fam:
            return salary_text.strip()
        # сначала русскоязычный рынок (если есть вилки из [telegram].cis_channels), затем глобальный; сначала тот же грейд
        for market, label in MARKETS:
            need = MIN_SAMPLES_CIS if market == "cis" else MIN_SAMPLES
            for lvl in ([level, ""] if level else [""]):
                pool = self._pool(fam, lvl, market)
                if len(pool) >= need:
                    mids = sorted((s["low"] + s["high"]) / 2 for s in pool)
                    q = statistics.quantiles(mids, n=4) if len(mids) >= 2 else [mids[0]] * 3
                    who = f"{lvl + ' ' if lvl else ''}{FAMILY_NAMES[fam]}"
                    return f"≈ {_fmt(round(q[0]), round(q[2]))}/мес (оценка: {who}, {len(pool)} вакансий, {label})"
        return salary_text.strip()

    def summary(self) -> list[str]:
        """Сводка базы для отчёта: медианные вилки по семействам и грейдам."""
        lines = []
        for market, label in MARKETS:
            groups: dict[tuple[str, str], list[dict]] = {}
            for s in self.samples.values():
                if s["market"] == market:
                    groups.setdefault((s["family"], s["seniority"] or "без грейда"), []).append(s)
            parts = []
            for (fam, level), pool in sorted(groups.items(), key=lambda kv: -len(kv[1])):
                if len(pool) < (MIN_SAMPLES_CIS if market == "cis" else MIN_SAMPLES) or fam not in ("backend", "fullstack", "ml", "data", "devops"):
                    continue
                low = statistics.median(s["low"] for s in pool)
                high = statistics.median(s["high"] for s in pool)
                parts.append(f"{level} {FAMILY_NAMES[fam]} {_fmt(round(low), round(high))} ({len(pool)})")
            if parts:
                lines.append(f"{label}: " + "; ".join(parts[:8]))
        return lines
