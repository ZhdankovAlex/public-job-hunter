"""Резюме под вакансию: отбор фактов из cv/base.toml -> Markdown-прототип -> HTML / PDF (гарвардский шаблон).

Ничего не придумывается: summary собирается по шаблону из достижений базы, навыки и пункты опыта
отбираются и сортируются по совпадению с вакансией (технологии из словаря cv_match.TECH_RX и темы
из TOPICS). Markdown — для проверки и правки руками, PDF собирается из него.
"""
from __future__ import annotations

import html
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

from . import config, cv_match, geo
from .config import ROOT

BASE_FILE = config.CV_BASE_FILE
OUT_DIR = ROOT / "cv_out"
# Chrome для PDF: JOBHUNTER_CHROME (контейнер: /usr/bin/chromium) > settings.toml [cv].chrome > путь в Windows
CHROME = os.environ.get("JOBHUNTER_CHROME") or geo.S.get("cv", {}).get(
    "chrome", r"C:\Program Files\Google\Chrome\Application\chrome.exe")

# Темы вакансии -> признаки в тексте; у пунктов базы темы заданы вручную (topics)
TOPICS = {
    "llm": r"\bllms?\b|\brag\b|genai|generative|ai agents?|agentic|retrieval|embedding|vector",
    "ai_tools": r"copilot|cursor|claude|ai[\s-]assisted|ai coding|ai tools",
    "performance": r"performance|latency|optimi[sz]|throughput|fast",
    "highload": r"high[\s-]?load|scal(?:e|able|ability|ing)|millions? of|large[\s-]scale|traffic",
    "security": r"security|rbac|authori[sz]ation|authentication|permissions?|compliance|access control",
    "realtime": r"real[\s-]?time|websocket|streaming|low[\s-]latency",
    "reliability": r"reliab|resilien|fault[\s-]toleran|incident|on[\s-]call|availability|self[\s-]healing",
    "observability": r"observab|monitoring|metrics|alerting|prometheus|grafana",
    "migrations": r"migrat|zero[\s-]downtime|schema changes?",
    "sql": r"\bsql\b|postgres|database|query",
    "api": r"\bapis?\b|rest(?:ful)?|graphql|endpoints?|integrations?",
    "distributed": r"distributed|microservices?|event[\s-]driven|message (?:queue|broker)",
    "messaging": r"kafka|rabbitmq|queue|pub/sub|event",
    "platform": r"platform|internal tools|developer experience|infrastructure",
    "devops": r"deploy|ci/cd|kubernetes|docker|packag|release",
    "quality": r"testing|tests?\b|code review|quality",
    "architecture": r"architect|design(?:ing)? systems|system design|monolith",
    "product": r"product|customers?|users?\b|features?",
    "integrations": r"integrat|third[\s-]party|partners?",
}
_TOPIC_RX = {name: re.compile(rx, re.I) for name, rx in TOPICS.items()}
# всегда в строке технологий под местом работы
# технологии, которые всегда видны в строке стека места работы — cv/base.toml core_tech
DEFAULT_CORE_TECH = ["Python"]


def core_tech(base: dict) -> set[str]:
    return set(base.get("core_tech", DEFAULT_CORE_TECH))


def ai_only_tech(base: dict) -> set[str]:
    """Технологии только для AI-версии резюме — cv/base.toml ai_only_tech."""
    return set(base.get("ai_only_tech", []))
SKILLS_MIN_PER_GROUP = 3  # меньше — группа навыков выглядит пустой


def load_base() -> dict:
    return tomllib.loads(BASE_FILE.read_text(encoding="utf-8"))


def _techs(text: str) -> set[str]:
    return {name for name, rx in cv_match.TECH_RX.items() if rx.search(text)}


_WORD_RX = re.compile(r"[a-z][a-z+#.-]{2,}")
_STOP = set("""the and for with from that this into over your our you are was were has have had will can
all any not but per via its their them they who what when where which while also than then such only more most
using used use work working team teams role experience years strong ability across within about""".split())
SIMILARITY_WEIGHT = 4.0  # сколько очков даёт полное текстовое совпадение пункта с вакансией (косинус = 1)


def _stem(word: str) -> str:
    """Грубая основа слова: scaling / scaled / scales -> scal."""
    for suffix in ("ations", "ation", "ings", "ing", "ers", "ies", "ed", "es", "er", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word


def _tokens(text: str) -> list[str]:
    return [_stem(w) for w in _WORD_RX.findall(text.lower()) if w not in _STOP]


class _Similarity:
    """TF-IDF-близость пунктов базы к тексту вакансии: ловит совпадения по смыслу без точного
    ключевого слова («latency» в вакансии — пункт про «load time»), которых нет в TECH_RX и TOPICS.

    IDF считается по пунктам базы: частые для всего резюме слова («PostgreSQL», «built») весят меньше.
    """

    def __init__(self, items: list[str], vacancy_text: str):
        import math
        from collections import Counter

        docs = [Counter(_tokens(t)) for t in items]
        df = Counter(w for d in docs for w in d)
        n = len(docs) or 1
        self.idf = {w: math.log((n + 1) / (c + 1)) + 1 for w, c in df.items()}
        self.vac = self._vector(Counter(_tokens(vacancy_text)))

    def _vector(self, counts) -> dict[str, float]:
        import math
        vec = {w: (1 + math.log(c)) * self.idf.get(w, 1.0) for w, c in counts.items()}
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        return {w: v / norm for w, v in vec.items()}

    def __call__(self, text: str) -> float:
        from collections import Counter
        item = self._vector(Counter(_tokens(text)))
        return sum(v * self.vac.get(w, 0.0) for w, v in item.items())


class Vacancy:
    """Что важно в вакансии: технологии, темы, AI-ли это роль, текстовая близость пунктов базы."""

    def __init__(self, title: str, text: str, base: dict | None = None):
        full = f"{title}\n{text}"
        self.title, self.text = title, text
        self.techs = _techs(full)
        self.topics = {name for name, rx in _TOPIC_RX.items() if rx.search(full)}
        self.is_ai = bool(cv_match._AI_TITLE_RX.search(title) or len(cv_match._AI_TEXT_RX.findall(text)) >= 3)
        items = [] if base is None else [
            *(a["text"] for a in base["summary"]["achievements"]),
            *(b["text"] for job in base["experience"] for b in job["bullets"]),
        ]
        self.similarity = _Similarity(items, full) if items and text.strip() else None

    def score(self, text: str, topics=()) -> float:
        """Насколько пункт базы подходит вакансии: технология — 2 очка, тема — 1, текстовая близость — до 4."""
        sim = SIMILARITY_WEIGHT * self.similarity(text) if self.similarity else 0.0
        return 2 * len(_techs(text) & self.techs) + len(set(topics) & self.topics) + sim

    def explain(self, text: str, topics=()) -> dict:
        """Из чего сложился балл пункта — для подсказки «почему выбран» в веб-интерфейсе."""
        return {
            "techs": sorted(_techs(text) & self.techs),
            "topics": sorted(set(topics) & self.topics),
            "similarity": round(self.similarity(text), 3) if self.similarity else 0.0,
            "score": round(self.score(text, topics), 2),
        }


def _allowed(item: dict, vac: Vacancy) -> bool:
    """requires = [темы]: пункт только для AI-ролей или вакансий, где эти темы есть (AI-инструменты в резюме
    под Kafka-бэкенд — шум)."""
    requires = item.get("requires")
    return not requires or vac.is_ai or bool(set(requires) & vac.topics)


def _pick(items: list[dict], vac: Vacancy, low: int, high: int) -> list[dict]:
    """pin — всегда; остальные по убыванию совпадения (при равенстве — порядок базы), от low до high штук."""
    items = [i for i in items if _allowed(i, vac)]
    pinned = [i for i in items if i.get("pin")]
    rest = [i for i in items if not i.get("pin")]
    ranked = sorted(rest, key=lambda i: -vac.score(i["text"], i.get("topics", [])))
    scored = [i for i in ranked if vac.score(i["text"], i.get("topics", [])) > 0]
    chosen = pinned + scored
    for i in rest:  # добираем до минимума в порядке базы
        if len(chosen) >= low:
            break
        if i not in chosen:
            chosen.append(i)
    return chosen[:high]


def _skills(base: dict, vac: Vacancy, everything: bool = False) -> list[tuple[str, str]]:
    """Группы навыков; everything — все пункты (базовое резюме)."""
    lines = []
    for group in base["skills"]:
        if group.get("only_ai") and not vac.is_ai or not _allowed(group, vac):
            continue
        everything = everything or vac.is_ai and group.get("only_ai") or (
            group["group"].startswith("AI-assisted") and "ai_tools" in vac.topics)
        items = [i["text"] for i in group["items"]
                 if everything or i.get("always") or _techs(i["text"]) & vac.techs]
        # вакансия называет мало технологий — добираем группу до минимума в порядке базы, чтобы не выглядеть пусто
        for i in group["items"]:
            if len(items) >= SKILLS_MIN_PER_GROUP:
                break
            if i["text"] not in items:
                items.append(i["text"])
        if items:
            lines.append((group["group"], ", ".join(items)))
    return lines


def _top_groups(groups: list[tuple[str, str]], vac: "Vacancy", limit: int | None) -> list[tuple[str, str]]:
    """Не больше limit групп навыков: оставляем с наибольшим числом технологий вакансии, порядок базы сохраняем."""
    if not limit or len(groups) <= limit:
        return groups
    ranked = sorted(range(len(groups)), key=lambda i: (-len(_techs(groups[i][1]) & vac.techs), i))
    keep = set(ranked[:limit])
    return [g for i, g in enumerate(groups) if i in keep]


# Как назвать роль в заголовке CV, если так её называет вакансия. Только честные синонимы профиля
# по умолчанию (backend на Python); всё остальное (Java, AI Engineer, Full Stack…) не подставляется никогда.
# Уровень (Senior, Lead…) берётся из заголовка в cv/base.toml.
# Должности в разделе Experience не меняются — там настоящие названия.
HEADLINE_ROLES = [
    (re.compile(r"python (?:back[\s-]?end )?developer", re.I), "Python Developer"),
    (re.compile(r"python (?:back[\s-]?end )?engineer", re.I), "Python Engineer"),
    (re.compile(r"back[\s-]?end developer", re.I), "Backend Developer"),
    (re.compile(r"back[\s-]?end (?:software )?engineer", re.I), "Backend Engineer"),
    (re.compile(r"software (?:engineer|developer)", re.I), "Software Engineer (Backend)"),
]


def headline_for(title: str, headline: str) -> str:
    """Заголовок CV под название вакансии: «Senior Backend Engineer | Python · …» -> «Senior Software Engineer
    (Backend) | Python · …», если вакансия называется Software Engineer. Нет совпадения — заголовок как в базе."""
    role, sep, rest = headline.partition(" | ")
    level = re.match(r"(?:senior|sr\.?|lead|staff|principal|middle|mid-level|junior)\s", role, re.I)
    for rx, name in HEADLINE_ROLES:
        if rx.search(title or ""):
            name = (level.group(0) if level else "") + name
            return f"{name}{sep}{rest}" if sep else name
    return headline


def _cap_items(items: str, limit: int | None) -> str:
    """«a, b, c, d» -> первые limit навыков (они уже отсортированы: обязательные и совпавшие — первыми)."""
    if not limit:
        return items
    parts = [p.strip() for p in re.split(r",\s*(?![^()]*\))", items)]  # запятые внутри скобок не делят
    return ", ".join(parts[:limit])


GENERIC_VARIANTS = ("backend", "ai")


def _generic_vacancy(variant: str) -> Vacancy:
    """«Вакансия» для базового резюме: AI-вариант открывает AI-пункты, backend — нет."""
    vac = Vacancy("", "")
    vac.is_ai = variant == "ai"
    vac.topics = {"ai_tools", "llm"} if vac.is_ai else set()
    return vac


def _for_variant(item: dict, variant: str | None) -> bool:
    """generic = ["backend"] — пункт только для базового резюме этого варианта (например, короткая версия AI-пункта)."""
    only = item.get("generic")
    return not only if variant is None else (not only or variant in only)


class Limits:
    """Сколько чего класть в резюме. Для варианта на одну страницу лимиты ужимаются (см. build_cv).

    Attributes:
        achievements: достижений в Summary.
        bullets: пунктов опыта по позиции в [[experience]] (0 — последнее место работы);
            нет позиции — bullets_max из базы.
        intro_paragraphs: абзацев описания компании и роли.
        skill_items: навыков в группе (None — без ограничения).
        publications: раздел Publications.
    """

    def __init__(self, compact: bool = False):
        self.achievements = 2 if compact else None
        # на одну страницу: последнее место работы — до 6 пунктов, предыдущее — до 3, остальные — до 2
        self.bullets: dict[int, int] = {0: 6, 1: 3} if compact else {}
        self.bullets_rest = 2 if compact else None
        self.intro_paragraphs = 1 if compact else None
        self.skill_items = 7 if compact else None
        self.skill_groups: int | None = None  # групп навыков (самые совпадающие с вакансией)
        self.publications = not compact

    def shrink(self) -> bool:
        """Убирает ещё один наименее важный кусок; False — ужимать больше нечего.

        Порядок: сначала группы навыков, меньше всего совпадающие с вакансией, потом пункты опыта.
        """
        if (self.skill_groups or 99) > 5:
            self.skill_groups = 6 if self.skill_groups is None else self.skill_groups - 1
        elif self.bullets.get(0, 0) > 4:
            self.bullets[0] -= 1
        elif self.bullets.get(1, 0) > 2:
            self.bullets[1] -= 1
        elif (self.skill_items or 99) > 5:
            self.skill_items = 5
        elif self.bullets.get(0, 0) > 3:
            self.bullets[0] -= 1
        elif self.bullets.get(1, 0) > 1:
            self.bullets[1] -= 1
        elif (self.bullets_rest or 0) > 1:
            self.bullets_rest -= 1
        elif (self.achievements or 3) > 1:
            self.achievements = 1
        else:
            return False
        return True


def build_markdown(base: dict, company: str, title: str, text: str, url: str = "", fit: int = -1,
                   missing: list | None = None, generic: str | None = None, limits: Limits | None = None,
                   picks: list | None = None) -> str:
    """Markdown-прототип резюме под вакансию; generic = "backend" / "ai" — базовое резюме без вакансии.

    В базовом резюме — все пункты и навыки в порядке базы (AI-пункты только в "ai"), в summary —
    достижения с generic, где указан вариант.

    Args:
        limits: лимиты разделов (вариант на одну страницу); None — обычное резюме.
        picks: если передан список, в него складывается, какие пункты выбраны и почему (балл, совпадения).
    """
    limits = limits or Limits()
    vac = _generic_vacancy(generic) if generic else Vacancy(title, text, base)
    summary_cfg = base["summary"]
    if generic:
        achievements = [a for a in summary_cfg["achievements"]
                        if a.get("pin") or generic in a.get("summary_for", [])]
    else:
        achievements = _pick([a for a in summary_cfg["achievements"] if _for_variant(a, None)], vac,
                             min(2, limits.achievements or 2),
                             limits.achievements or summary_cfg.get("max_achievements", 3))
        if picks is not None:
            picks += [{"section": "Summary", "text": a["text"], "pin": bool(a.get("pin")),
                       **vac.explain(a["text"], a.get("topics", []))} for a in achievements]
    links = " · ".join(f"[{label}]({href})" for label, href in base["links"])
    header = ([f"<!-- базовое резюме: {'Backend + AI' if generic == 'ai' else 'Backend'}",
               "     Собрать PDF после правок: python cvgen.py --render <этот файл> -->"] if generic else [
        f"<!-- вакансия: {company} — {title}" + (f" — {url}" if url else ""),
        f"     Fit: {fit}%" if fit >= 0 else "     Fit: не посчитан",
        f"     не хватает ключевых: {', '.join(missing)}" if missing else "     ключевые навыки из словаря закрыты",
        f"     технологии вакансии: {', '.join(sorted(vac.techs)) or '—'}; темы: {', '.join(sorted(vac.topics)) or '—'}",
        "     Проверь текст, поправь при необходимости и собери PDF: python cvgen.py --render <этот файл> -->",
    ])
    md = [
        *header,
        "",
        f"# {base['name']}",
        (base["headline"]["ai" if vac.is_ai else "backend"] if generic
         else headline_for(title, base["headline"]["ai" if vac.is_ai else "backend"])),
        base["contacts"],
        links,
        "",
        "## Summary",
        " ".join([summary_cfg["intro"], *(a["text"] for a in achievements), summary_cfg["closing"]]),
        "",
        "## Skills",
        *(f"- **{group}:** {_cap_items(items, limits.skill_items)}"
          for group, items in _top_groups(_skills(base, vac, everything=bool(generic)), vac, limits.skill_groups)),
        "",
        "## Experience",
    ]
    for position, job in enumerate(base["experience"]):
        high = job.get("bullets_max", 6)
        cap = limits.bullets.get(position, limits.bullets_rest)
        high = min(high, cap) if cap is not None else high
        if generic:
            bullets = [b for b in job["bullets"] if _allowed(b, vac) and _for_variant(b, generic)]
            tech = [t for t in job["tech"] if vac.is_ai or t not in ai_only_tech(base)]
        else:
            bullets = _pick([b for b in job["bullets"] if _for_variant(b, None)], vac, min(5, high), high)
            tech = [t for t in job["tech"] if t in core_tech(base) or t in job.get("tech_always", [])
                    or _techs(t) & vac.techs or (vac.is_ai and t in ai_only_tech(base))]
            if picks is not None:
                picks += [{"section": job["company"], "text": b["text"], "pin": bool(b.get("pin")),
                           **vac.explain(b["text"], b.get("topics", []))} for b in bullets]
        md += ["", f"### {job['title']} — {job['company']}", f"*{job['dates']}*", ""]
        md += [*job["intro"][: limits.intro_paragraphs], ""]
        md += [f"- {b['text']}" for b in bullets]
        md += ["", f"_{' · '.join(tech)}_"]
    edu = base["education"]
    md += [
        *(["", "## Publications", base["publications"]["text"]] if limits.publications else []),
        "", "## Education", f"**{edu['degree']}** · {edu['years']}  ",
        edu["school"] + ("  " if vac.is_ai and limits.publications else ""),
        *([edu["thesis_ai"]] if vac.is_ai and limits.publications else []),  # в варианте на страницу — без темы диплома
        "", "## Languages", base["languages"]["text"], "",
    ]
    return "\n".join(md)


# --- Markdown -> HTML: classic (как резюме на рабочем столе) и harvard ---------------------------

STYLE = geo.S.get("cv", {}).get("style", "classic")

# Arial, тёмно-синие заголовки, линия под шапкой, курсивные даты под должностью
CLASSIC_CSS = """
@page { size: A4; margin: 14mm 15mm 14mm 15mm; }
* { box-sizing: border-box; }
/* один акцентный цвет (#1f3a5f) для имени, строки под ним, разделов и должностей; серый #666 — вторичное */
body { font-family: Arial, Helvetica, sans-serif; font-size: 9.6pt; line-height: 1.32; color: #111; margin: 0; }
a { color: inherit; text-decoration: none; }
h1 { font-size: 22pt; font-weight: 700; color: #1f3a5f; letter-spacing: 0.3px; margin: 0; line-height: 1.1; }
.headline { color: #1f3a5f; font-size: 11.5pt; margin: 3px 0 4px; }
.contacts { color: #666; font-size: 9pt; line-height: 1.45; }
.rule { border-bottom: 1.2px solid #1f3a5f; margin: 7px 0 9px; }
h2 { font-size: 11pt; font-weight: 700; color: #1f3a5f; text-transform: uppercase; letter-spacing: 0.4px;
     margin: 10px 0 4px; break-after: avoid; }
h3 { font-size: 10.2pt; font-weight: 700; color: #1f3a5f; margin: 8px 0 0; }
.dates { color: #666; font-style: italic; font-size: 9pt; margin: 0 0 4px; }
.job-head { break-inside: avoid; break-after: avoid; }
p { margin: 0 0 5px; }
ul { list-style: none; margin: 3px 0 4px 16px; padding: 0; }
li { margin: 0 0 4px; break-inside: avoid; }
li::before { content: "\\2022\\00a0"; }
.tech { color: #666; font-style: italic; font-size: 8.8pt; margin: 2px 0 6px; }
.skills p { margin: 0 0 3px; }
.keep { break-inside: avoid; }
"""

HARVARD_CSS = """
@page { size: A4; margin: 14mm 16mm; }
body { font-family: "Times New Roman", Georgia, serif; font-size: 10.6pt; line-height: 1.3; color: #000; margin: 0; }
a { color: inherit; text-decoration: none; }
h1 { text-align: center; font-size: 21pt; letter-spacing: 1.5px; margin: 0; font-weight: 700; }
.headline { text-align: center; font-size: 11.5pt; margin: 2px 0 2px; }
.contacts { text-align: center; font-size: 9.6pt; line-height: 1.4; }
h2 { font-size: 11pt; text-transform: uppercase; letter-spacing: .6px; border-bottom: 1px solid #000;
     margin: 11px 0 5px; padding-bottom: 1px; break-after: avoid; }
.job-head { display: flex; justify-content: space-between; align-items: baseline; margin: 7px 0 2px; break-after: avoid; }
.job-head b { font-size: 10.8pt; } .job-head i { white-space: nowrap; margin-left: 12px; }
p { margin: 0 0 4px; text-align: justify; }
ul { margin: 3px 0 3px 17px; padding: 0; } li { margin: 0 0 3px; text-align: justify; break-inside: avoid; }
.tech { font-style: italic; font-size: 9.4pt; margin: 2px 0 4px; }
.skills p { margin: 0 0 2px; }
.keep { break-inside: avoid; }
"""


# вариант на одну страницу: плотнее строки и поля, мельче шрифт (поверх classic / harvard)
COMPACT_CSS = """
@page { margin: 10mm 12mm; }
body { font-size: 9pt; line-height: 1.24; }
h1 { font-size: 21pt; } h2 { margin: 7px 0 3px; } h3 { margin: 6px 0 0; }
p { margin: 0 0 3px; } li { margin: 0 0 2px; } .tech { margin: 1px 0 3px; }
.rule { margin: 5px 0 6px; }
"""


def _inline(text: str) -> str:
    text = html.escape(text, quote=False)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)
    return text.replace("AES-256", "AES&#8209;256")


def markdown_to_html(md: str, style: str = STYLE, compact: bool = False) -> str:
    """Свой маленький разбор: только то, что пишет build_markdown (и правки в том же стиле).

    style: "classic" — оформление базовых резюме (Arial, синие заголовки), "harvard" — шрифт с засечками.
    """
    classic = style != "harvard"
    md = re.sub(r"<!--.*?-->", "", md, flags=re.S)
    lines = md.splitlines()
    out, ul, header, section = [], False, [], ""
    i = 0
    while i < len(lines):
        raw = lines[i]
        line = raw.strip()
        i += 1
        if ul and not line.startswith("- "):
            if re.fullmatch(r"_.+_", line) or (not line and i < len(lines) and re.fullmatch(r"_.+_", lines[i].strip())):
                # стек места работы — вместе с последним пунктом, чтобы не уехал один на новую страницу
                tech_line = line if line else lines[i].strip()
                if not line:
                    i += 1
                last = out.pop()
                out += ["</ul>", '<div class="keep"><ul>', last, "</ul>",
                        f'<div class="tech">{_inline(tech_line[1:-1])}</div></div>']
                ul = False
                continue
            out.append("</ul>")
            ul = False
        if not line:
            continue
        if line.startswith("# "):
            out.append(f"<h1>{_inline(line[2:])}</h1>")
            while i < len(lines) and lines[i].strip() and not lines[i].startswith("#"):
                header.append(lines[i].strip())
                i += 1
            if header:
                out.append(f'<div class="headline">{_inline(header[0])}</div>')
                out.append(f'<div class="contacts">{"<br>".join(_inline(h) for h in header[1:])}</div>')
                if classic:
                    out.append('<div class="rule"></div>')
        elif line.startswith("## "):
            if section == "Skills":
                out.append("</div>")
            section = line[3:].strip()
            out.append(f"<h2>{_inline(section)}</h2>")
            if section == "Skills":
                out.append('<div class="skills">')
        elif line.startswith("### "):
            dates = ""
            if i < len(lines) and re.fullmatch(r"\*[^*].*\*", lines[i].strip()):
                dates = lines[i].strip()[1:-1]
                i += 1
            if classic:
                out.append(f'<div class="job-head"><h3>{_inline(line[4:])}</h3><div class="dates">{_inline(dates)}</div></div>')
            else:
                out.append(f'<div class="job-head"><b>{_inline(line[4:])}</b><i>{_inline(dates)}</i></div>')
        elif line.startswith("- ") and section == "Skills":
            out.append(f"<p>{_inline(line[2:])}</p>")
        elif line.startswith("- "):
            if not ul:
                out.append("<ul>")
                ul = True
            out.append(f"<li>{_inline(line[2:])}</li>")
        elif re.fullmatch(r"_.+_", line):
            out.append(f'<div class="tech">{_inline(line[1:-1])}</div>')
        else:
            text = _inline(line) + ("<br>" if raw.endswith("  ") else "")
            if out and out[-1].endswith("<br>"):
                out[-1] += text  # строки, разделённые «  », — один абзац
            else:
                out.append(f"<p>{text}</p>")
    if ul:
        out.append("</ul>")
    if section == "Skills":
        out.append("</div>")
    body = "\n".join(out).replace("<br></p>", "</p>")
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'><title>CV</title>"
            f"<style>{CLASSIC_CSS if classic else HARVARD_CSS}{COMPACT_CSS if compact else ''}</style></head>"
            f"<body>\n{body}\n</body></html>")


def render(md_path: Path, pdf: bool = True, style: str = STYLE, compact: bool | None = None) -> tuple[Path, Path | None]:
    """md -> html рядом; pdf — через Chrome без окна (как уже собирались резюме).

    compact — плотная вёрстка варианта на одну страницу; None — по имени файла (…_1p.md).
    """
    if compact is None:
        compact = md_path.stem.endswith("_1p")
    html_path = md_path.with_suffix(".html")
    html_path.write_text(markdown_to_html(md_path.read_text(encoding="utf-8"), style, compact), encoding="utf-8")
    if not pdf:
        return html_path, None
    pdf_path = md_path.with_suffix(".pdf")
    # печать во временный файл и подмена: Chrome молча не перезаписывает PDF, открытый в просмотрщике,
    # и тогда оставался старый файл — теперь это ошибка с понятным текстом
    tmp_path = md_path.with_suffix(".tmp.pdf").resolve()  # Chrome считает относительный путь от своей папки
    tmp_path.unlink(missing_ok=True)
    subprocess.run(
        # в контейнере Chrome работает от root — без песочницы он не запускается
        [CHROME, "--headless", "--disable-gpu", "--no-pdf-header-footer",
         *(["--no-sandbox"] if sys.platform != "win32" else []),
         f"--print-to-pdf={tmp_path}", html_path.resolve().as_uri()],
        check=True, capture_output=True, timeout=120,
    )
    if not tmp_path.exists():
        raise RuntimeError(f"Chrome не создал PDF ({CHROME})")
    try:
        os.replace(tmp_path, pdf_path)
    except PermissionError as e:
        tmp_path.unlink(missing_ok=True)
        raise RuntimeError(f"{pdf_path.name} открыт в другой программе — закройте его и соберите заново") from e
    return html_path, pdf_path


def _rel(path: Path) -> str:
    """Путь от корня проекта (так на файлы ссылается веб-интерфейс), а вне проекта — полный."""
    return path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else str(path)


def pdf_pages(pdf_path: Path) -> int:
    from pypdf import PdfReader
    return len(PdfReader(str(pdf_path)).pages)


ONE_PAGE_ATTEMPTS = 8  # сколько раз ужимать вариант на одну страницу


def build_cv(company: str, title: str, text: str, url: str = "", one_page: bool = False,
             out_dir: Path | None = None, style: str = STYLE) -> dict:
    """Резюме под вакансию целиком: Fit, Markdown, PDF; one_page — ужимать, пока не влезет в одну страницу.

    Args:
        company, title, text, url: вакансия (текст — как можно полнее: по нему идёт подбор).
        one_page: вариант на одну страницу (короче Summary и описание компаний, меньше пунктов и навыков).
        out_dir: куда класть файлы; по умолчанию cv_out/<дата>/.

    Returns:
        {md, pdf, html, pages, fit, missing, techs, topics, picks, one_page_fit} — пути относительно ROOT.
    """
    from datetime import date

    holder = type("Job", (), {"title": title, "company": company, "mismatch_lang": ""})()
    cv_match.CvProfile(geo.S.get("cv", {})).analyze(holder, text)
    base = load_base()
    out_dir = out_dir or OUT_DIR / date.today().isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)
    name = f"{slug(company)}_{slug(title)}" + ("_1p" if one_page else "")
    md_path = out_dir / f"{name}.md"
    limits = Limits(compact=one_page)
    pages = 0
    for _ in range(ONE_PAGE_ATTEMPTS if one_page else 1):
        picks: list = []
        md_path.write_text(build_markdown(base, company, title, text, url, holder.cv_fit, holder.cv_core_missing,
                                          limits=limits, picks=picks), encoding="utf-8")
        html_path, pdf_path = render(md_path, style=style, compact=one_page)
        pages = pdf_pages(pdf_path)
        if not one_page or pages <= 1 or not limits.shrink():
            break
    vac = Vacancy(title, text)
    return {
        "md": _rel(md_path), "pdf": _rel(pdf_path), "html": _rel(html_path),
        "pages": pages, "one_page_fit": pages <= 1,
        "fit": holder.cv_fit, "missing": list(holder.cv_core_missing),
        "techs": sorted(vac.techs), "topics": sorted(vac.topics), "picks": picks,
    }


AUTO = geo.S.get("cv", {})
AUTO_FIT_MIN = int(AUTO.get("auto_fit_min", 80))
AUTO_MAX = int(AUTO.get("auto_max", 40))


def auto_build(jobs: list, day) -> int:
    """Резюме под каждую новую вакансию с Fit ≥ AUTO_FIT_MIN (категории Backend и ML / AI).

    Кладёт .md и .pdf в cv_out/auto/<дата>/, путь — в job.cv_file. Не больше AUTO_MAX за прогон
    (самые подходящие первыми). -> сколько собрано.
    """
    import logging

    from . import tiers

    from . import progress

    log = logging.getLogger(__name__)
    picked = [j for j in jobs if j.is_new_today and getattr(j, "cv_fit", -1) >= AUTO_FIT_MIN
              and j.tier in (tiers.BACKEND, tiers.ML) and not getattr(j, "staffing", "")
              and not getattr(j, "lang_required", False)]
    picked.sort(key=lambda j: (-j.cv_fit, -j.relevance))
    if not picked:
        return 0
    out_dir = OUT_DIR / "auto" / day.isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)
    base = load_base()
    built = 0
    progress.set_total(len(picked[:AUTO_MAX]))
    for j in picked[:AUTO_MAX]:
        if progress.skip_requested():
            break
        progress.step(f"{j.company} — {j.title}")
        text = getattr(j, "description", "") or getattr(j, "text", "")
        md_path = out_dir / f"{slug(j.company)}_{slug(j.title)}.md"
        try:
            md_path.write_text(build_markdown(base, j.company, j.title, text, j.url or getattr(j, "post_url", ""),
                                              j.cv_fit, j.cv_core_missing), encoding="utf-8")
            _, pdf_path = render(md_path)
        except Exception as e:  # одно резюме не должно ронять отчёт
            log.warning("резюме под %s — %s: %s", j.company, j.title, e)
            continue
        j.cv_file = pdf_path.relative_to(ROOT).as_posix()
        built += 1
    log.info("Резюме под вакансии: собрано %d (Fit ≥ %d%%, новые, Backend / ML) -> %s", built, AUTO_FIT_MIN, out_dir)
    return built


_TRANSLIT = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                     ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s",
                      "t", "u", "f", "h", "ts", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya"]))


def slug(text: str) -> str:
    """Имя файла из названия: кириллица — транслитом («Запускай Технологии» -> Zapuskay_Tehnologii)."""
    text = "".join(_TRANSLIT.get(ch.lower(), ch).capitalize() if ch.isupper() and ch.lower() in _TRANSLIT
                   else _TRANSLIT.get(ch, ch) for ch in text)
    return re.sub(r"[^\w]+", "_", text, flags=re.ASCII).strip("_")[:40] or "cv"
