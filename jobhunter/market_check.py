"""«Резюме против рынка»: локальный ATS-чекер по вакансиям из отчёта.

Сравнивает резюме не с абстрактным списком навыков, а с подходящими вакансиями конкретного дня
(state/jobs/<дата>.json): какие технологии рынок требует и есть ли они в PDF-резюме и в базе
cv/base.toml, как называются вакансии и совпадает ли с этим заголовок резюме, и простая гигиена
пунктов опыта (цифры, глагол в начале, длина). Ничего никуда не отправляет.
"""
from __future__ import annotations

import json
import re
import statistics
import tomllib
from collections import Counter
from pathlib import Path

from . import config, cv_match, geo

# какие вакансии считать «своим рынком»: роль по названию
RELEVANT_TITLE_RX = re.compile(r"back[\s-]?end|python|software engineer|software developer|platform engineer|"
                               r"\bai engineer|\bml engineer|llm|full[\s-]?stack", re.I)
# как называются роли: фраза -> регулярка по названию вакансии
TITLE_PHRASES = {
    "Backend Engineer": r"back[\s-]?end (?:software )?engineer",
    "Backend Developer": r"back[\s-]?end developer",
    "Python Developer": r"python (?:back[\s-]?end )?developer",
    "Python Engineer": r"python (?:back[\s-]?end )?engineer",
    "Software Engineer": r"software engineer",
    "Software Developer": r"software developer",
    "Platform Engineer": r"platform engineer",
    "AI Engineer": r"\b(?:ai|ml|llm) engineer",
    "Full Stack": r"full[\s-]?stack",
}
SENIORITY = {"Senior": r"\bsenior\b|\bsr\.?\b", "Lead": r"\blead\b", "Staff": r"\bstaff\b",
             "Middle": r"\bmiddle\b|\bmid\b", "Principal": r"\bprincipal\b"}
ACTION_VERBS = set("""architected built designed developed reduced cut migrated introduced automated separated
implemented led rebuilt integrated delivered created improved optimized launched owned raised replaced moved
scaled wrote set established drove""".split())
_NUMBER_RX = re.compile(r"\d")
# слова, по которым резюме «звучит как ИИ» или как пересказ обязанностей (чек-лист из канала @zerotooffer)
_STOP_RX = re.compile(r"\b(?:helped|(?<!the )supported|assisted|leveraged|leveraging|utilized|utilizing|various|basic|"
                      r"responsible for|worked on|participated in|spearheaded|cutting-edge|seamless(?:ly)?|"
                      r"robust|synergy|dynamic environment|passionate)\b", re.I)
SAME_FORM_SHARE = 0.8  # доля пунктов «глагол в начале» — выше: все пункты одной формы
NUMBERS_PER_JOB_SOFT = 4  # больше пунктов с цифрами на одно место работы — может выглядеть накрученным
BULLET_WORDS_MAX = 45  # длиннее — пункт читается тяжело, кандидат на сокращение
TOP_KEYWORDS = 30


def _cv_texts() -> tuple[str, str, list[Path]]:
    """(текст PDF-резюме, текст базы cv/base.toml, пути к PDF) — то же, что читает cv_match."""
    settings = geo.S.get("cv", {})
    profile = cv_match.CvProfile(settings)
    pdf_text = "\n".join(t for _, t in profile.cvs)
    base_file = config.CV_BASE_FILE
    base_text = base_file.read_text(encoding="utf-8") if base_file.exists() else ""
    return pdf_text, base_text, [Path(p) for p in settings.get("files") or []]


def _bullets(base_text: str) -> list[str]:
    """Пункты опыта из базы, без разметки **жирного**."""
    try:
        base = tomllib.loads(base_text)
    except tomllib.TOMLDecodeError:
        return []
    return [re.sub(r"\*\*", "", b["text"]) for job in base.get("experience", []) for b in job.get("bullets", [])
            if not b.get("generic")]


def hygiene(base_text: str, pdfs: list[Path], pdf_text: str) -> dict:
    """Проверки резюме: страницы PDF, пункты с цифрами / глаголом / короткие, контакты, заголовок."""
    from pypdf import PdfReader

    bullets = _bullets(base_text)
    first_words = [re.sub(r"[^a-z]", "", b.split()[0].lower()) if b.split() else "" for b in bullets]
    words = [len(b.split()) for b in bullets]
    pages = {}
    for path in pdfs:
        if path.exists():
            pages[path.name] = len(PdfReader(str(path)).pages)
    try:
        base = tomllib.loads(base_text)
    except tomllib.TOMLDecodeError:
        base = {}
    texts = bullets + [a["text"] for a in base.get("summary", {}).get("achievements", [])]
    stop = sorted({m.group(0).lower() for t in texts for m in _STOP_RX.finditer(t)})
    # цифры по местам работы: «результат в цифрах» хорошо, но сплошные цифры выглядят накрученными
    numbers_by_job = {job["company"]: sum(bool(_NUMBER_RX.search(b["text"])) for b in job.get("bullets", [])
                                          if not b.get("generic"))
                      for job in base.get("experience", [])}
    return {
        "stop_words": stop,
        "numbers_by_job": numbers_by_job,
        "pages": pages,
        "bullets": len(bullets),
        "with_numbers": sum(bool(_NUMBER_RX.search(b)) for b in bullets),
        "with_verb": sum(w in ACTION_VERBS for w in first_words),
        "long": [b[:90] + "…" for b, n in zip(bullets, words) if n > BULLET_WORDS_MAX],
        "avg_words": round(statistics.mean(words)) if words else 0,
        "no_numbers": [b[:90] + "…" for b in bullets if not _NUMBER_RX.search(b)],
        "contacts": {
            "email": bool(re.search(r"[\w.+-]+@[\w-]+\.\w+", pdf_text)),
            "LinkedIn": "linkedin.com/in/" in pdf_text.lower(),
            "телефон": bool(re.search(r"\+\d[\d\s()-]{7,}", pdf_text)),
            "часовой пояс / локация": bool(re.search(r"\b(?:CET|CEST|GMT|UTC|[A-Z]{2,4}T)\b|time zone", pdf_text)
                                           or config.HOME_RX.search(pdf_text)),
        },
    }


def analyze(day: str = "") -> dict:
    """Отчёт «резюме против рынка» по вакансиям дня day (по умолчанию — последнего отчёта).

    Returns:
        {day, total, relevant, fit: {median, share80}, keywords: [...], titles: [...], seniority: [...],
         headline, hygiene}. keywords: технология, доля вакансий, где она ключевая, где она в резюме
         ("pdf" — в PDF-резюме, "base" — только в базе, "" — нигде).
    """
    files = sorted(config.JOBS_ARCHIVE_DIR.glob("*.json"))
    path = next((f for f in files if f.stem == day), files[-1] if files else None)
    if path is None:
        return {"day": "", "total": 0, "relevant": 0}
    jobs = json.loads(path.read_text(encoding="utf-8"))
    relevant = [j for j in jobs if RELEVANT_TITLE_RX.search(j["title"]) and j.get("text")]
    pdf_text, base_text, pdfs = _cv_texts()
    profile = cv_match.CvProfile(geo.S.get("cv", {}))

    core = Counter()
    fits = []
    for job in relevant:
        holder = type("Job", (), {"title": job["title"], "company": job["company"], "mismatch_lang": ""})()
        profile.analyze(holder, job["text"])
        fits.append(holder.cv_fit)
        core.update(n for n in [*holder.cv_core_have, *holder.cv_core_missing] if n in cv_match.TECH_RX)

    def where(name: str) -> str:
        rx = cv_match.TECH_RX[name]
        alias = cv_match.CV_ALIASES.get(name)
        if rx.search(pdf_text) or (alias and re.search(alias, pdf_text, re.I)):
            return "pdf"
        if rx.search(base_text) or (alias and re.search(alias, base_text, re.I)):
            return "base"
        return ""

    n = len(relevant) or 1
    keywords = [{"name": name, "count": c, "share": round(100 * c / n), "in_cv": where(name),
                 "partial": cv_match.PARTIAL.get(name, "")}  # AWS: только S3 — есть, но с оговоркой
                for name, c in core.most_common(TOP_KEYWORDS)]
    titles = Counter()
    levels = Counter()
    for job in relevant:
        for phrase, rx in TITLE_PHRASES.items():
            if re.search(rx, job["title"], re.I):
                titles[phrase] += 1
        for level, rx in SENIORITY.items():
            if re.search(rx, job["title"], re.I):
                levels[level] += 1
    try:
        headlines = tomllib.loads(base_text).get("headline", {})
    except tomllib.TOMLDecodeError:
        headlines = {}
    headline = " / ".join(headlines.values())
    measured = [f for f in fits if f >= 0]
    return {
        "day": path.stem, "total": len(jobs), "relevant": len(relevant),
        "fit": {"median": round(statistics.median(measured)) if measured else None,
                "share80": round(100 * sum(f >= 80 for f in measured) / len(measured)) if measured else 0},
        "keywords": keywords,
        "titles": [{"name": k, "count": v, "share": round(100 * v / n),
                    "in_headline": bool(re.search(TITLE_PHRASES[k], headline, re.I))} for k, v in titles.most_common()],
        "seniority": [{"name": k, "count": v, "share": round(100 * v / n)} for k, v in levels.most_common()],
        "headline": headline,
        "hygiene": hygiene(base_text, pdfs, pdf_text),
        "days": [f.stem for f in reversed(files)],
    }


def advice(report: dict) -> list[str]:
    """Выводы для страницы: что поправить в резюме, по убыванию пользы."""
    out = []
    # названия ролей: честные синонимы бэкенда генератор CV подставляет сам (cvgen.HEADLINE_ROLES),
    # остальное (AI Engineer, Full Stack, Platform) — не переименование, а другой профиль
    synonyms = {"Software Engineer", "Software Developer", "Python Developer", "Python Engineer",
                "Backend Developer", "Backend Engineer"}
    missing = [t for t in report.get("titles", []) if not t["in_headline"] and t["share"] >= 10]
    same_role = [t for t in missing if t["name"] in synonyms]
    other_role = [t for t in missing if t["name"] not in synonyms]
    if same_role:
        top = same_role[0]
        names = ", ".join(f"«{t['name']}» ({t['share']}%)" for t in same_role)
        out.append(f"Так же называют твою роль, но в заголовке этого нет: {names}. Рекрутеры и ATS ищут по названию "
                   f"роли. Поправь заголовок LinkedIn (по нему тебя находят) и базовых PDF, например: «Senior Backend / "
                   f"{top['name']} | Python · PostgreSQL · …». В резюме по кнопке «📄 CV» это уже делается само: "
                   "заголовок подстраивается под название вакансии.")
    if other_role:
        names = ", ".join(f"«{t['name']}» ({t['share']}%)" for t in other_role)
        out.append(f"Частые, но другие роли: {names}. Переименовываться не нужно. Если есть близкий опыт (например, "
                   "RAG / LLM для AI-ролей), на бэкенд-позиции в таких командах можно откликаться: генератор CV для "
                   "AI-вакансий ставит заголовок с LLM / RAG.")
    in_base = [k for k in report.get("keywords", []) if k["in_cv"] == "base" and k["share"] >= 5]
    if in_base:
        out.append("Есть в базе cv/base.toml, но нет в PDF-резюме: " + ", ".join(
            f"{k['name']} ({k['share']}%)" for k in in_base) + " — пересоберите базовые резюме (cvgen.py --base …).")
    gaps = [k for k in report.get("keywords", []) if not k["in_cv"] and k["share"] >= 10]
    if gaps:
        out.append("Пробелы — требуются часто, а в резюме нет: " + ", ".join(
            f"{k['name']} ({k['share']}%)" for k in gaps) + ". Это про опыт, не про формулировки: если опыт есть — "
            "добавьте в базу, если нет — кандидаты на изучение.")
    partial = [k for k in report.get("keywords", []) if k["partial"] and k["share"] >= 10]
    if partial:
        out.append("Есть частично: " + ", ".join(f"{k['name']} — {k['partial']} ({k['share']}%)" for k in partial) + ".")
    h = report.get("hygiene", {})
    long_cvs = [name for name, pages in h.get("pages", {}).items() if pages > 1]
    if long_cvs:
        out.append(f"PDF-резюме больше одной страницы ({', '.join(long_cvs)}). Для западного рынка при 6 годах "
                   "опыта лучше одна — у кнопки «1 стр.» в отчёте и на странице «Резюме» вариант на страницу.")
    if h.get("stop_words"):
        out.append("Слова, по которым резюме звучит как пересказ обязанностей или как ИИ: "
                   + ", ".join(f"«{w}»" for w in h["stop_words"]) + " — замените конкретным действием и результатом.")
    if h.get("bullets") and h["with_verb"] >= SAME_FORM_SHARE * h["bullets"]:
        out.append(f"Все пункты одной формы: {h['with_verb']} из {h['bullets']} начинаются с глагола в прошедшем "
                   "времени. Однообразие выдаёт шаблон (и ИИ) — начните часть пунктов с результата или с проблемы, "
                   "добавьте пару коротких.")
    heavy = [f"{job} ({n})" for job, n in h.get("numbers_by_job", {}).items() if n > NUMBERS_PER_JOB_SOFT]
    if heavy:
        out.append(f"Цифры почти в каждом пункте: {', '.join(heavy)} пунктов с цифрами. Измеримый результат — сильно, "
                   "но сплошные цифры могут выглядеть накрученными: оставьте самые весомые, в остальных — масштаб "
                   "или кто этим пользуется. (Мнения расходятся: часть советов — «цифры везде», часть — «не больше "
                   "двух-трёх на место работы».)")
    elif h.get("bullets") and h["with_numbers"] < h["bullets"] / 2:
        out.append(f"Пунктов с измеримым результатом мало: {h['with_numbers']} из {h['bullets']} — где результат "
                   "есть (время, объём, доля), его стоит показать.")
    if h.get("long"):
        out.append(f"Длинные пункты (больше {BULLET_WORDS_MAX} слов): {len(h['long'])} — на них тратят 5 секунд, "
                   "лучше одна мысль на пункт.")
    return out
