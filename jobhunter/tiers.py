"""Категория вакансии — первый ключ ранжирования; внутри категории — свежесть, затем балл.

1. Backend      — backend / Python / Software Engineer продукта
2. ML / AI      — ML, AI, LLM-инженерия, Data Scientist
3. DevOps / платформа — DevOps, SRE, инфраструктура, платформенные роли, «эксплуатация» по тексту
4. Прочее       — full-stack, frontend и вакансии, где ключевой навык — стек не из резюме
                  (TypeScript, Node.js, JavaScript, React, Go, Java…), а также data/analytics-роли
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from . import config as c

BACKEND, ML, OPS, OTHER = 1, 2, 3, 4
NAMES = {BACKEND: "1 · Backend", ML: "2 · ML / AI", OPS: "3 · DevOps / платформа", OTHER: "4 · Прочее"}

_FULLSTACK_RX = re.compile(r"full[\s-]?stack|front[\s-]?end|фулстек|фронтенд|\bweb developer\b", re.I)
_ML_RX = re.compile(r"\bml\b|\bai\b|\bllms?\b|machine learning|genai|data scientist|deep learning|\bnlp\b|computer vision", re.I)
_DATA_RX = re.compile(r"data engineer|analytics engineer|data analyst|\bbi\b|data platform", re.I)
_BACKEND_TITLE_RX = re.compile(r"back[\s-]?end|б[эе]к[\s-]?енд|python|django|fastapi", re.I)
# ключевой стек, которого нет в резюме: если он в «не хватает» — вакансия не по стеку
FOREIGN_STACK = {"TypeScript", "JavaScript", "Node.js", "React", "Vue", "Angular", "Go", "Java", "Kotlin",
                 "Ruby", "PHP", ".NET / C#", "Scala", "Rust", "Elixir"}


def tier(job) -> int:
    """Категория вакансии по названию, тексту (штрафы verify) и сопоставлению с резюме."""
    title = job.title
    missing = set(getattr(job, "cv_missing", []))
    if _FULLSTACK_RX.search(title) or (missing & FOREIGN_STACK) or getattr(job, "mismatch_lang", ""):
        return OTHER
    if _BACKEND_TITLE_RX.search(title):
        return BACKEND  # «Backend Platform Engineer», «Python Developer (AI)» — всё равно backend
    if c.OPS_ROLE_RX.search(title) or c.PLATFORM_TITLE_RX.search(title) or "ops_heavy" in getattr(job, "penalties", []):
        return OPS
    if _DATA_RX.search(title):
        return OTHER
    if _ML_RX.search(title):
        return ML
    return BACKEND


# границы групп свежести, в днях: вчерашняя вакансия с пробелами в навыках важнее июльской
# с полным совпадением — на свежую выше шанс получить приглашение
AGE_BUCKETS = (3, 7, 14, 30)
AGE_NAMES = ("до 3 дней", "до недели", "до 2 недель", "до месяца", "старше месяца")
UNKNOWN_AGE_BUCKET = 3  # дата публикации неизвестна — считаем, что вакансии до месяца


def age_bucket(job) -> int:
    """Номер группы свежести: 0 — опубликована за последние 3 дня, 4 — старше месяца."""
    if not job.posted:
        return UNKNOWN_AGE_BUCKET
    posted = job.posted if job.posted.tzinfo else job.posted.replace(tzinfo=timezone.utc)
    days = (datetime.now(timezone.utc) - posted).days
    return next((i for i, limit in enumerate(AGE_BUCKETS) if days <= limit), len(AGE_BUCKETS))


def sort_key(job) -> tuple:
    """Категория, затем явно низкая вилка (вниз), затем группа свежести, затем балл, затем точная дата."""
    from . import salary

    return (
        getattr(job, "tier", OTHER), salary.below_min(job), age_bucket(job), -job.relevance,
        -(job.posted.timestamp() if job.posted else 0),
    )
