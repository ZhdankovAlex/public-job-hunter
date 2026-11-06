"""Подготовка к интервью под конкретную вакансию — из фактов cv/base.toml, ничего не придумывает.

Что собирается:
- требования вакансии, которые есть в опыте, и пункт опыта, который это доказывает;
- требования, которых в опыте нет (честно сказать и показать близкое);
- черновик «рассказа о себе» на английском: текущая роль → компания одной фразой → задачи языком
  вакансии → совпадающий стек → прошлый опыт короче → «про какой проект рассказать подробнее?»;
- истории из банка [[stories]] под типовые поведенческие вопросы, по совпадению с вакансией;
- чек-лист к HR-звонку (зарплата — с медианой levels.fyi по стране вакансии) и вопросы к ним.
"""
from __future__ import annotations

import re

from . import config, cv_match, cvgen, geo

_FROM_HOME = f" from {config.HOME_COUNTRY}" if config.HOME_COUNTRY else ""

# типовые поведенческие вопросы на собеседованиях

BEHAVIORAL = {
    "initiative": "Tell me about something you changed in a product or service on your own initiative. Why?",
    "early_detection": "Was there a case when you noticed a problem before users or management did?",
    "pushback": "Tell me about a time you proposed a different solution instead of doing what was asked.",
    "research": "Tell me about a technical decision you had to make in an area you didn't know well.",
    "incident": "Tell me about a production incident you were involved in. What was your role?",
    "ownership": "Tell me about the riskiest change you owned end to end.",
}
QUESTIONS_TO_ASK = [
    "Which team would I join, and what is it building right now? What would my first 3 months look like?",
    "What does the stack look like day to day — languages, databases, messaging, deployment?",
    "How do you use AI coding tools in the team, and what is the policy around generated code?",
    "How are decisions made: who owns architecture, how do code reviews work?",
    f"Working format: fully remote{_FROM_HOME}? Contract (B2B) or employment, and through which entity?",
    "What are the next interview stages and the timeline for a decision?",
]
HR_CHECKLIST = [
    "Why are you looking: say what you are moving towards (product, scale, team), not what you are running from.",
    f"What matters in an offer: product company, remote{_FROM_HOME}, backend in Python, growth in the role.",
    "Salary: name a number near the top of their range — negotiating is expected (see the market line below).",
    "Prepare 2–3 own questions (below) and check camera and sound 10 minutes before.",
]


def _plain(text: str) -> str:
    return re.sub(r"\*\*", "", text).strip()


def _headline(bullet: str) -> str:
    """Короткая суть пункта — его жирный заголовок: «**Built a … service** for …» -> «built a … service»."""
    m = re.search(r"\*\*(.+?)\*\*", bullet)
    text = (m.group(1) if m else _first_sentence(_plain(bullet))).strip().rstrip(":.")
    return text[0].lower() + text[1:]


def _first_sentence(text: str) -> str:
    return re.split(r"(?<=[.!?])\s", text.strip(), maxsplit=1)[0]


def build(company: str, title: str, text: str, location: str = "") -> dict:
    """Материалы к интервью: {fit, have: [{tech, proof}], missing, pitch, stories, behavioral, hr, ask, market}.

    Args:
        company, title, text: вакансия (текст — чем полнее, тем точнее подбор).
        location: локация вакансии — для медианы levels.fyi в подсказке про зарплату.
    """
    from .levels import Levels

    base = cvgen.load_base()
    vac = cvgen.Vacancy(title, text, base)
    holder = type("Job", (), {"title": title, "company": company, "mismatch_lang": ""})()
    cv_match.CvProfile(geo.S.get("cv", {})).analyze(holder, text)

    bullets = [(job, b) for job in base["experience"] for b in job["bullets"] if not b.get("generic")]
    ranked = sorted(bullets, key=lambda jb: -vac.score(jb[1]["text"], jb[1].get("topics", [])))

    # требование -> лучший пункт опыта, где эта технология есть
    have = []
    for tech in holder.cv_core_have:
        proof = next((f"{job['company']}: {_plain(b['text'])}" for job, b in ranked
                      if tech in cvgen._techs(b["text"])), "")
        have.append({"tech": tech, "proof": proof or "есть в навыках резюме"})

    # «рассказ о себе»: всё из базы, порядок — по структуре
    current, previous = base["experience"][0], base["experience"][-1]
    top = [_headline(b["text"]) for job, b in ranked if job is current][:3]
    stack = [t for t in current["tech"] if t in vac.techs or t in cvgen.core_tech(base)][:6]
    intro = _first_sentence(current["intro"][0])
    pitch = [
        f"I'm a {current['title']} at {current['company']}.",
        f"{current['company']} is a {intro[0].lower()}{intro[1:]}",
        "There I " + "; ".join(top) + ".",
        f"Day to day I work with {', '.join(stack)}." if stack else "",
        f"Before that I spent almost four years at {previous['company']} as a {previous['title']}, where I "
        + _headline(previous["bullets"][0]["text"]) + ".",
        "Which project would you like me to go deeper into?",
    ]

    stories = sorted(base.get("stories", []), key=lambda s: -(len(set(s.get("topics", [])) & vac.topics)))
    behavioral = []
    for key, question in BEHAVIORAL.items():
        fit = [s["title"] for s in stories if key in s.get("questions", [])][:2]
        behavioral.append({"question": question, "stories": fit})

    market, market_url = Levels().describe(location) if location else ("", "")
    return {
        "company": company, "title": title, "fit": holder.cv_fit,
        "have": have, "missing": list(holder.cv_core_missing), "partial": list(holder.cv_partial),
        "mismatch": list(holder.cv_mismatch),
        "pitch": " ".join(p for p in pitch if p),
        "stories": stories[:4], "behavioral": behavioral,
        "hr": HR_CHECKLIST, "ask": QUESTIONS_TO_ASK, "market": market, "market_url": market_url,
    }
