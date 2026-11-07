"""Сопроводительное письмо под вакансию — из фактов cv/base.toml, ничего не придумывает.

Как и cvgen: достижения из [summary] и soft skills из [cover] ранжируются под текст вакансии
(технологии, темы, текстовая близость), письмо собирается по шаблону:
кто вы как инженер → чем полезны (hard) → как работаю (soft) → честно, чего нет → «давайте созвонимся».

Стили:
- short — тезисно: вступление в одну строку, список «что принесу», призыв к созвону;
- full — абзацами: вступление, достижения (списком — пункты резюме написаны без «I»), стек и soft skills
  абзацем, чего в опыте нет.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from . import cv_match, cvgen, geo

STYLES = ("short", "full")

# если в base.toml нет [cover] — нейтральные формулировки (без фактов, которых нет в базе)
DEFAULT_COVER = {
    "greeting": "Hi {company} team,",
    "opening": "I'm interested in the {title} role at {company}: it matches what I've been doing.",
    "lead_short": "What I can bring:",
    "lead_full": "Most relevant to your role:",
    "soft": [],
    "missing": "I haven't worked with {missing} in production yet and would be happy to ramp up on it.",
    "closing": "I'd be glad to have a call and discuss the role in more detail.",
    "sign": "Best regards,",
}


def _name(base: dict) -> str:
    return " ".join(w.capitalize() for w in base["name"].split())


def _ranked(items: list[dict], vac: cvgen.Vacancy) -> list[dict]:
    return sorted(items, key=lambda i: -vac.score(i["text"], i.get("topics", [])))


def build(company: str, title: str, text: str, style: str = "short") -> dict:
    """Письмо под вакансию: {text, style, fit, have, missing, picks}.

    Args:
        company, title, text: вакансия (текст — чем полнее, тем точнее подбор).
        style: short — тезисно, full — абзацами.
    """
    if style not in STYLES:
        raise ValueError(f"style: {style!r}, ожидается {STYLES}")
    base = cvgen.load_base()
    cover = {**DEFAULT_COVER, **base.get("cover", {})}
    vac = cvgen.Vacancy(title, text, base)
    holder = type("Job", (), {"title": title, "company": company, "mismatch_lang": ""})()
    cv_match.CvProfile(geo.S.get("cv", {})).analyze(holder, text)
    fmt = {"company": company, "title": title}

    achievements = cvgen._pick([a for a in base["summary"]["achievements"] if cvgen._for_variant(a, None)],
                               vac, 3, 3 if style == "short" else 4)
    soft_items = [s if isinstance(s, dict) else {"text": s} for s in cover["soft"]]
    soft = [s for s in _ranked([s for s in soft_items if cvgen._allowed(s, vac)], vac)][:2 if style == "short" else 3]
    have = list(holder.cv_core_have)
    missing = list(holder.cv_core_missing)[:2]

    lines = [cover["greeting"].format(**fmt), ""]
    intro = cover.get("intro") or base["summary"]["intro"]
    if style == "short":
        lines += [f"{intro} {cover['opening'].format(**fmt)}", "", cover["lead_short"].format(**fmt)]
        lines += [f"- {a['text']}" for a in achievements]
        if have:
            lines.append(f"- Stack overlap with the role: {', '.join(have[:8])}.")
        lines += [f"- {s['text']}" for s in soft]
    else:
        lines += [f"{intro} {cover['opening'].format(**fmt)}", ""]
        # пункты резюме написаны без «I» — в связном тексте читаются криво, поэтому и здесь списком
        lines += [cover["lead_full"].format(**fmt), *(f"- {a['text']}" for a in achievements), ""]
        stack = f"My experience covers {', '.join(have[:8])} from your stack." if have else ""
        paragraph = " ".join(p for p in [stack, *(s["text"] for s in soft)] if p)
        if paragraph:
            lines += [paragraph, ""]
        if missing:
            text_missing = cover["missing"].format(missing=" and ".join(missing), **fmt)
            if len(missing) > 1:
                text_missing = text_missing.replace(" on it", " on them").replace(" with it", " with them")
            lines += [text_missing, ""]
        if lines[-1] == "":
            lines.pop()
    lines += ["", cover["closing"].format(**fmt), "", cover["sign"].format(**fmt), _name(base)]

    picks = [{"section": "Достижение", "text": a["text"], **vac.explain(a["text"], a.get("topics", []))}
             for a in achievements]
    picks += [{"section": "Soft skills", "text": s["text"], **vac.explain(s["text"], s.get("topics", []))}
              for s in soft]
    return {"company": company, "title": title, "style": style, "text": "\n".join(lines),
            "fit": holder.cv_fit, "have": have, "missing": list(holder.cv_core_missing), "picks": picks}


def save(result: dict, url: str = "", out_dir: Path | None = None) -> Path:
    """Письмо в cv_out/<дата>/<компания>_<роль>_cover_<стиль>.txt — рядом с резюме под ту же вакансию."""
    out_dir = out_dir or cvgen.OUT_DIR / date.today().isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{cvgen.slug(result['company'])}_{cvgen.slug(result['title'])}_cover_{result['style']}.txt"
    path.write_text(result["text"] + "\n", encoding="utf-8")
    return path
