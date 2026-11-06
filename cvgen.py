"""Резюме под вакансию из cv/base.toml.

  python cvgen.py <ссылка или часть названия компании / вакансии>   — из последнего отчёта (state/jobs_latest.json)
  python cvgen.py --text vacancy.txt --company X --title "Senior Backend Engineer"   — из текста вакансии
  python cvgen.py --render cv_out/2026-10-23/X_Senior.md   — пересобрать PDF после правок в .md
  --one-page — вариант на одну страницу (ужимается, пока не влезет)

Результат — cv_out/<дата>/<компания>_<вакансия>.md (прототип для проверки) и .pdf рядом.
То же из веб-интерфейса: кнопка «📄 CV» в отчёте или страница «Резюме».
"""
import argparse
import json
import sys
from pathlib import Path

from jobhunter import config, cvgen

JOBS_FILE = config.ROOT / "state" / "jobs_latest.json"


def find_job(query: str) -> dict:
    jobs = json.loads(JOBS_FILE.read_text(encoding="utf-8")) if JOBS_FILE.exists() else []
    exact = [j for j in jobs if j["url"] and j["url"].rstrip("/") == query.rstrip("/")]
    if exact:
        return exact[0]
    q = query.lower()
    found = [j for j in jobs if q in f"{j['company']} {j['title']}".lower()]
    if len(found) == 1:
        return found[0]
    if not found:
        sys.exit(f"Вакансия «{query}» не найдена в {JOBS_FILE.name} (последний отчёт). "
                 "Дайте текст вакансии: --text файл --company … --title …")
    print("Нашлось несколько вакансий — уточните запрос или дайте ссылку:")
    for j in found[:20]:
        print(f"  {j['company']} — {j['title']} — {j['url']}")
    sys.exit(1)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("query", nargs="?", help="ссылка на вакансию или часть названия компании / роли")
    p.add_argument("--text", type=Path, help="файл с текстом вакансии (если её нет в отчёте)")
    p.add_argument("--company", default="", help="компания (с --text)")
    p.add_argument("--title", default="", help="название вакансии (с --text)")
    p.add_argument("--render", type=Path, help="пересобрать HTML / PDF из отредактированного .md")
    p.add_argument("--no-pdf", action="store_true", help="только .md и .html (кроме сборки под вакансию)")
    p.add_argument("--one-page", action="store_true", help="вариант на одну страницу")
    p.add_argument("--base", choices=cvgen.GENERIC_VARIANTS, help="базовое резюме без вакансии: backend или ai")
    p.add_argument("--style", choices=("classic", "harvard"), default=cvgen.STYLE,
                   help="оформление: classic (как базовые резюме) или harvard")
    args = p.parse_args()

    if args.render:
        html_path, pdf_path = cvgen.render(args.render, pdf=not args.no_pdf, style=args.style)
        print(f"Готово: {pdf_path or html_path}")
        return
    if args.base:
        person = "_".join(w.capitalize() for w in cvgen.load_base()["name"].split())  # имя из cv/base.toml
        name = f"{person}_Backend_AI_CV" if args.base == "ai" else f"{person}_Backend_CV"
        cvgen.OUT_DIR.mkdir(exist_ok=True)
        md_path = cvgen.OUT_DIR / f"{name}.md"
        md_path.write_text(cvgen.build_markdown(cvgen.load_base(), "", "", "", generic=args.base), encoding="utf-8")
        html_path, pdf_path = cvgen.render(md_path, pdf=not args.no_pdf, style=args.style)
        print(f"Базовое резюме: {md_path}\nPDF: {pdf_path or html_path}")
        return
    if args.text:
        job = {"company": args.company or "Company", "title": args.title or "Vacancy", "url": "",
               "text": args.text.read_text(encoding="utf-8")}
    elif args.query:
        job = find_job(args.query)
    else:
        p.error("нужна ссылка / запрос, --text или --render")

    # Fit, подбор пунктов и (для --one-page) ужимание до одной страницы — как у кнопки в веб-интерфейсе
    result = cvgen.build_cv(job["company"], job["title"], job["text"], job.get("url", ""),
                            one_page=args.one_page, style=args.style)
    print(f"Fit {result['fit']}% · страниц {result['pages']} · прототип: {result['md']}\nPDF: {result['pdf']}")


if __name__ == "__main__":
    main()
