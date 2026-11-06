"""Новые события откликов из веб-интерфейса -> applications_manual.toml.

Файл тот же, что ведётся руками: событие дописывается в конец отдельным блоком [[application]]
(несколько блоков одной компании складываются при чтении — mail.manual_events). Существующие
строки и комментарии не трогаются. После записи файл проверяется tomllib; если что-то пошло не так,
он возвращается как был.
"""
from __future__ import annotations

import json
import tomllib
from datetime import datetime

from . import config

KINDS = {"applied": "Отклик", "interview": "Этап / собеседование", "rejected": "Отказ", "offer": "Оффер",
         "other": "Заметка"}


def _toml_str(value: str) -> str:
    """Строка TOML: экранирование JSON подходит (\\", \\\\, \\n, \\uXXXX)."""
    return json.dumps(value, ensure_ascii=False)


def add_event(company: str, kind: str, note: str = "", url: str = "", source: str = "",
              when: datetime | None = None) -> dict:
    """Дописывает событие отклика.

    Args:
        company: компания (как в отчёте — по ней событие склеится с письмами).
        kind: applied / interview / rejected / offer / other.
        note: что произошло, своими словами.
        url: ссылка на вакансию — в отчётах она будет помечена «🔁 уже откликался».
        source: откуда отклик (сайт компании, LinkedIn, Telegram…).
        when: время события; по умолчанию сейчас.

    Returns:
        Записанное событие.

    Raises:
        ValueError: неизвестный kind или пустая компания.
    """
    company = company.strip()
    if not company:
        raise ValueError("Нужна компания")
    if kind not in KINDS:
        raise ValueError(f"kind — один из {', '.join(KINDS)}")
    when = (when or datetime.now()).replace(microsecond=0, tzinfo=None)
    fields = [f"date = {when.isoformat()}", f"kind = {_toml_str(kind)}", f"note = {_toml_str(note.strip())}"]
    if url.strip():
        fields.append(f"url = {_toml_str(url.strip())}")
    block = (f"\n[[application]]\ncompany = {_toml_str(company)}\n"
             f"source = {_toml_str(source.strip() or 'веб-интерфейс')}\n"
             f"events = [\n  {{ {', '.join(fields)} }},\n]\n")
    path = config.MANUAL_APPLICATIONS_FILE
    before = path.read_text(encoding="utf-8") if path.exists() else ""
    path.write_text(before.rstrip("\n") + "\n" + block, encoding="utf-8")
    try:
        tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError:
        path.write_text(before, encoding="utf-8")
        raise
    return {"company": company, "kind": kind, "note": note.strip(), "url": url.strip(), "date": when.isoformat()}


def companies() -> list[str]:
    """Компании из applications_manual.toml и кэша писем — подсказки в форме."""
    from . import mail

    names = set()
    if config.MANUAL_APPLICATIONS_FILE.exists():
        data = tomllib.loads(config.MANUAL_APPLICATIONS_FILE.read_text(encoding="utf-8"))
        names |= {e["company"] for e in data.get("application", [])}
    try:
        apps, _ = mail.summarize(mail.cached_messages())
        names |= {a.company for a in apps}
    except Exception:  # кэша писем нет — подсказки только из файла
        pass
    return sorted(names, key=str.lower)
