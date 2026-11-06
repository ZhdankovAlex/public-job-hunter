"""Статусы откликов из почты: читает Gmail (только чтение) и собирает reports/applications.html.

Запуск: .venv\\Scripts\\python.exe applications.py
Первый запуск откроет окно входа Google в браузере (нужен gmail_client.json, см. docs/applications.md).
Если Gmail недоступен, страница собирается из кэша писем с предупреждением, а код выхода — 1.
"""
import logging
import sys
from datetime import date

from jobhunter import applications_report, applied, config, mail, progress

STAGES = [
    ("mail", "Gmail: новые письма"),
    ("applied", "Отклики из applications_manual.toml"),
    ("render", "Страница откликов"),
]


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
        handlers=[progress.ConsoleHandler(), logging.FileHandler(config.ERRORS_LOG, encoding="utf-8")],
    )
    pipeline = progress.Pipeline("applications", STAGES, skippable={"mail"})
    try:
        error = run()
    except BaseException:
        logging.getLogger("applications").exception("Прогон прерван")
        pipeline.finish(ok=False)
        raise
    skipped = progress.was_skipped("mail")  # после finish() прогона уже нет
    pipeline.finish()
    if error and not skipped:  # пропуск по кнопке — не ошибка
        sys.exit(1)


def run() -> str:
    """Почта -> отклики -> страница; возвращает текст ошибки почты или ''."""
    # библиотека входа Google пишет в INFO ссылку и одноразовый код авторизации — в лог им не место
    logging.getLogger("google_auth_oauthlib").setLevel(logging.WARNING)
    log = logging.getLogger("applications")
    error = ""
    progress.start("mail")
    try:
        messages, address = mail.fetch_messages()
    except Exception as e:  # отчёт важнее: остальное работает и на кэше, а о проблеме предупредит страница
        log.exception("почта не обновилась")
        error = f"{type(e).__name__}: {e}"[:200]
        messages, address = mail.cached_messages(), "(не обновилась)"
    if progress.was_skipped("mail") and not error:
        # письма, прочитанные до кнопки, уже в кэше; время синхронизации не обновляем — почта неполная
        error = "чтение почты пропущено вручную"
        log.warning("%s: остальные письма — в следующий раз", error)
    mail.save_sync(error)
    out, apps, unknown = build_page(messages, address)
    log.info("Готово: компаний %d, писем без компании %d -> %s", len(apps), len(unknown), out)
    return error


def build_page(messages: list[dict] | None = None, address: str = "(из кэша писем)"):
    """reports/applications.html из писем (по умолчанию — из кэша, без Gmail) и откликов из файлов.

    Веб-интерфейс вызывает её после нового события: страница обновляется за доли секунды.
    -> (путь, компании, письма без компании).
    """
    if messages is None:
        messages = mail.cached_messages()
    apps, unknown = mail.summarize(messages)
    progress.count(companies=len(apps), unknown_letters=len(unknown))
    progress.start("applied")
    index = None if "--no-applied" in sys.argv else applied.load_applications()
    progress.start("render")
    config.REPORTS_DIR.mkdir(exist_ok=True)
    page = applications_report.render(date.today(), apps, unknown, address, index, mail.stale_warning())
    out = config.REPORTS_DIR / "applications.html"
    out.write_text(page, encoding="utf-8")
    return out, apps, unknown


if __name__ == "__main__":
    main()
