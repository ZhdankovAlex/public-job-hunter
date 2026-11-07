"""Локальный веб-интерфейс job-hunter: пайплайн прогона, архив отчётов, отклики, источники.

Слушает только 127.0.0.1: в отчётах почта, отклики и контакты, наружу их не отдаём.
Запуск: .venv\\Scripts\\python.exe -m web  (откроет браузер; документация API — /docs).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from typing import Literal

from pydantic import BaseModel, Field

from jobhunter import config, cover_letter, cvgen, interview_prep, manual_apps, market_check, progress, relevance, vacancy_lookup

ROOT = config.ROOT
WEB = Path(__file__).parent
REPORTS = config.REPORTS_DIR
SOURCES_FILE = ROOT / "state" / "sources_latest.json"
FREEHIRE_FILE = ROOT / "state" / "freehire_latest.json"
RUN_LOG = ROOT / "last_run.log"
_REPORT_RX = re.compile(r"^jobs_(\d{4}-\d{2}-\d{2})\.html$")

app = FastAPI(
    title="job-hunter",
    description="Локальный интерфейс к скрипту поиска работы: прогресс прогона, отчёты, отклики, источники. "
                "Скрипт ничего не отправляет наружу; сервер слушает только 127.0.0.1.",
    version="1.0",
)
templates = Jinja2Templates(directory=WEB / "templates")
app.mount("/static", StaticFiles(directory=WEB / "static"), name="static")
REPORTS.mkdir(exist_ok=True)
(ROOT / "cv_out").mkdir(exist_ok=True)
# отчёты ссылаются на резюме как ../cv_out/…, поэтому обе папки висят рядом под /files
app.mount("/files/reports", StaticFiles(directory=REPORTS), name="reports")
app.mount("/files/cv_out", StaticFiles(directory=ROOT / "cv_out"), name="cv_out")

# (ключ, подпись, иконка) — иконка видна и в свёрнутой левой панели
# (адрес, подпись, эмодзи-иконка; те же значки, что у кнопок отчёта: 📬 отклики, 📄 CV, 🎤 подготовка)
NAV = [("pipeline", "Пайплайн", "⚙️"), ("reports", "Отчёты", "📋"), ("applications", "Отклики", "📬"),
       ("cv", "Резюме", "📄"), ("cover", "Письмо", "✉️"), ("prep", "Подготовка", "🎤"), ("market", "Резюме vs рынок", "⚖️"),
       ("skills", "Навыки", "🛠️"), ("sources", "Источники", "🌐")]


# --- запуск прогона ---------------------------------------------------------------------------

class Runner:
    """Один прогон за раз: скрипты по очереди в фоновом потоке, вывод — в last_run.log."""

    SCRIPTS = {
        "all": ["applications.py", "main.py"],  # как run_daily.bat: сначала почта, потом вакансии
        "main": ["main.py"],
        "applications": ["applications.py"],
    }
    # что можно выключить заранее -> флаг main.py; «mail» убирает applications.py из очереди
    SKIP_FLAGS = {"telegram": "--no-telegram", "freehire": "--no-freehire", "aggregators": "--no-aggregators"}

    def __init__(self):
        self.lock = threading.Lock()
        self.process: subprocess.Popen | None = None
        self.thread: threading.Thread | None = None
        self.queue: list[str] = []
        self.stopping = False

    def busy(self) -> bool:
        """Идёт ли прогон: запущенный отсюда или из терминала / Планировщика."""
        if self.thread and self.thread.is_alive():
            return True
        data = progress.read()
        return bool(data and progress.is_alive(data))

    def start(self, kind: str, skip: list[str]) -> list[str]:
        """Запускает очередь скриптов; skip — этапы, выключенные заранее. -> команды без интерпретатора."""
        with self.lock:
            if self.busy():
                raise HTTPException(409, "Прогон уже идёт — дождитесь конца")
            scripts = [s for s in self.SCRIPTS[kind] if not ("mail" in skip and s == "applications.py")]
            if not scripts:
                raise HTTPException(422, "Нечего запускать: почта выключена, а других скриптов в очереди нет")
            flags = [self.SKIP_FLAGS[k] for k in skip if k in self.SKIP_FLAGS]
            commands = [[s, *flags] if s == "main.py" else [s] for s in scripts]
            self.queue = [" ".join(c) for c in commands]
            self.stopping = False
            self.thread = threading.Thread(target=self._run, args=(commands,), daemon=True)
            self.thread.start()
            return list(self.queue)

    def _run(self, commands: list[list[str]]) -> None:
        python = ROOT / ".venv" / "Scripts" / "python.exe"
        python = python if python.exists() else Path(sys.executable)
        with RUN_LOG.open("a", encoding="utf-8") as log:
            for i, command in enumerate(commands):
                log.write(f"\n=== {datetime.now():%Y-%m-%d %H:%M:%S} {' '.join(command)} (запуск из веб-интерфейса) ===\n")
                log.flush()
                # отдельная группа процессов: Ctrl+C в окне сервера не прерывает идущий прогон.
                # DETACHED_PROCESS не используем: с ним прогоны, запущенные из-под некоторых оболочек,
                # молча завершались на полпути. Перезапуск сервера по-прежнему прерывает прогон — см. docs/web-ui.md.
                detached = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
                self.process = subprocess.Popen(
                    [str(python), *command], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                    env={**os.environ, "PYTHONIOENCODING": "utf-8", **({progress.CONTINUE_RUN_ENV: "1"} if i else {})},
                    creationflags=detached,
                    start_new_session=sys.platform != "win32",
                )
                self.process.wait()
                self.queue.pop(0)
                if self.stopping:  # «Остановить прогон»: следующие скрипты очереди не запускаем
                    log.write(f"=== {datetime.now():%Y-%m-%d %H:%M:%S} остановлено из веб-интерфейса ===\n")
                    break
        self.queue = []
        self.process = None

    def stop(self) -> int:
        """Полная остановка: очередь сбрасывается, идущий скрипт завершается вместе с дочерними процессами.

        Работает и для прогона из терминала / Планировщика (pid из progress.json). -> pid остановленного.
        Собранное в этом прогоне не сохраняется: отчёты остаются от прошлого прогона.
        """
        with self.lock:
            data = progress.read()
            pid = self.process.pid if self.process and self.process.poll() is None else None
            if pid is None and data and progress.is_alive(data):
                pid = data["pid"]
            if pid is None:
                raise HTTPException(409, "Прогон не идёт")
            self.stopping = True
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
            else:
                import signal
                try:
                    os.killpg(pid, signal.SIGTERM)  # start_new_session: группа = pid
                except OSError:
                    os.kill(pid, signal.SIGTERM)
            return pid


runner = Runner()

SCHEDULER_FILE = ROOT / "state" / "scheduler.json"  # дата последнего запуска по расписанию


def _daily_scheduler(at: str) -> None:
    """Раз в день в at (ЧЧ:ММ, время контейнера / TZ) — прогон «всё», как run_daily.bat.

    Включается переменной JOBHUNTER_DAILY_AT (в docker-compose.yml); на Windows обычно не нужен —
    там Планировщик заданий и run_daily.bat. Пропущенное время (сервер был выключен) догоняется,
    если сегодня прогона по расписанию ещё не было; если прогон уже идёт — ждём его конца.
    """
    import time

    while True:
        now = datetime.now()
        try:
            last = json.loads(SCHEDULER_FILE.read_text(encoding="utf-8")).get("last", "")
        except (OSError, ValueError):
            last = ""
        if now.strftime("%H:%M") >= at and last != now.date().isoformat() and not runner.busy():
            try:
                runner.start("all", [])
                SCHEDULER_FILE.write_text(json.dumps({"last": now.date().isoformat()}), encoding="utf-8")
            except HTTPException:
                pass  # прогон уже идёт — попробуем через минуту
        time.sleep(60)


if os.environ.get("JOBHUNTER_DAILY_AT"):
    threading.Thread(target=_daily_scheduler, args=(os.environ["JOBHUNTER_DAILY_AT"],), daemon=True).start()


class RunRequest(BaseModel):
    """Что запустить."""
    kind: str = Field("all", description="all — почта, потом вакансии (как run_daily.bat); main — только "
                                          "вакансии; applications — только почта", pattern="^(all|main|applications)$")
    skip: list[Literal["mail", "telegram", "freehire", "aggregators"]] = Field(
        default_factory=list, description="Этапы, выключенные заранее: mail — не запускать applications.py; "
                                          "telegram / freehire / aggregators — флаги --no-… для main.py")


class RunStarted(BaseModel):
    scripts: list[str] = Field(description="Команды в порядке запуска")


class SkipRequest(BaseModel):
    """Какой этап идущего прогона пропустить."""
    stage: str = Field(description="Ключ этапа из /api/progress (stages[].key); этап должен идти и быть skippable")


# --- данные для страниц ------------------------------------------------------------------------

def progress_payload() -> dict | None:
    """progress.json + жив ли процесс + ETA всего прогона + очередь скриптов."""
    data = progress.read()
    if data is None:
        return None
    if data["status"] == progress.RUNNING and not progress.is_alive(data):
        data["status"] = "interrupted"
    data["eta_total"] = progress.eta_total(data)
    data["queue"] = list(runner.queue)
    data["busy"] = runner.busy()
    return data


def report_list() -> list[dict]:
    """Отчёты по датам, новые сверху: дата, файл, размер, есть ли Markdown-версия."""
    rows = []
    for path in REPORTS.glob("jobs_*.html"):
        m = _REPORT_RX.match(path.name)
        if m:
            stat = path.stat()
            rows.append({"date": m.group(1), "file": path.name, "size_kb": stat.st_size // 1024,
                         "built": datetime.fromtimestamp(stat.st_mtime).strftime("%H:%M"),
                         "md": (REPORTS / path.name.replace(".html", ".md")).exists()})
    return sorted(rows, key=lambda r: r["date"], reverse=True)


def sources_payload() -> dict:
    """Источники последнего прогона + Telegram-каналы + запросы freehire из настроек."""
    try:
        sources = json.loads(SOURCES_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        sources = {}
    built = datetime.fromtimestamp(SOURCES_FILE.stat().st_mtime) if SOURCES_FILE.exists() else None
    from jobhunter import geo
    rows = [{"name": k, **v} for k, v in sources.items()]
    rows.sort(key=lambda r: (-r["jobs"], r["name"].lower()))
    kinds: dict[str, dict] = {}
    for r in rows:
        k = kinds.setdefault(r["kind"], {"sources": 0, "jobs": 0, "empty": 0, "errors": 0})
        k["sources"] += 1
        k["jobs"] += r["jobs"]
        k["empty"] += r["jobs"] == 0 and not r.get("error")
        k["errors"] += bool(r.get("error"))
    return {
        "built": built.strftime("%d.%m %H:%M") if built else "",
        "rows": rows, "kinds": kinds,
        "telegram": list(config.TELEGRAM_CHANNELS) if config.TELEGRAM_ENABLED else [],
        "freehire": freehire_queries(geo.S.get("freehire", {})),
    }


def freehire_queries(settings: dict) -> list[dict]:
    """Запросы freehire: из state/freehire_latest.json, а до первого прогона — из settings.toml без итогов."""
    try:
        return json.loads(FREEHIRE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        skip = ("name", "title")
        return [{"name": q["name"], "title": q.get("title", q["name"]), "total": None, "fetched": None,
                 "params": {k: v for k, v in q.items() if k not in skip}, "skipped": False}
                for q in settings.get("queries", [])]


def recent_cvs(limit: int = 40) -> list[dict]:
    """Собранные резюме (PDF в cv_out/), новые сверху."""
    rows = []
    for pdf in (ROOT / "cv_out").rglob("*.pdf"):
        stat = pdf.stat()
        rel = pdf.relative_to(ROOT).as_posix()
        md = pdf.with_suffix(".md")
        # первая строка .md: «<!-- вакансия: Компания — Роль — https://…» (cvgen.build_markdown)
        head = md.read_text(encoding="utf-8")[:600] if md.exists() else ""
        m = re.search(r"<!-- вакансия: (.+?) — (.+?)(?: — (https?://\S+))?\s*$", head, re.M)
        rows.append({"name": f"{m.group(1)} — {m.group(2)}" if m else pdf.stem.replace("_", " "),
                     "vacancy": (m.group(3) or "") if m else "", "pdf": f"/files/{rel}",
                     "md": f"/files/{md.relative_to(ROOT).as_posix()}" if md.exists() else "",
                     "one_page": pdf.stem.endswith("_1p"), "mtime": stat.st_mtime,
                     "built": datetime.fromtimestamp(stat.st_mtime).strftime("%d.%m %H:%M")})
    return sorted(rows, key=lambda r: -r["mtime"])[:limit]


def _static_version() -> str:
    """Метка для ?v= у CSS / JS: время последней правки web/static — браузер не держит старые стили в кэше."""
    return str(int(max((f.stat().st_mtime for f in (WEB / "static").rglob("*.*")), default=0)))


def page(request: Request, name: str, **context) -> HTMLResponse:
    return templates.TemplateResponse(request, f"{name}.html",
                                      {"nav": NAV, "active": name, "v": _static_version(), **context})


# --- страницы ----------------------------------------------------------------------------------

@app.get("/", include_in_schema=False)
def index():
    return RedirectResponse("/pipeline")


@app.get("/pipeline", response_class=HTMLResponse, include_in_schema=False)
def pipeline_page(request: Request):
    return page(request, "pipeline")


@app.get("/reports", response_class=HTMLResponse, include_in_schema=False)
def reports_page(request: Request, file: str = ""):
    reports = report_list()
    if file and not any(r["file"] == file for r in reports):
        raise HTTPException(404, "Нет такого отчёта")
    current = file or (reports[0]["file"] if reports else "")
    return page(request, "reports", reports=reports, current=current)


@app.get("/applications", response_class=HTMLResponse, include_in_schema=False)
def applications_page(request: Request, company: str = "", title: str = "", url: str = "", kind: str = ""):
    exists = (REPORTS / "applications.html").exists()
    prefill = {"company": company, "title": title, "url": url, "kind": kind or ("applied" if url else "")}
    return page(request, "applications", exists=exists, prefill=prefill, kinds=manual_apps.KINDS,
                companies=manual_apps.companies())


@app.get("/cv", response_class=HTMLResponse, include_in_schema=False)
def cv_page(request: Request):
    return page(request, "cv", cvs=recent_cvs())


@app.get("/cover", response_class=HTMLResponse, include_in_schema=False)
def cover_page(request: Request):
    return page(request, "cover")


_market_cache: dict[tuple, dict] = {}


def market_report(day: str = "") -> dict:
    """market_check.analyze + выводы; пересчёт, только если поменялись вакансии дня или резюме (~8 с)."""
    files = sorted(config.JOBS_ARCHIVE_DIR.glob("*.json"))
    path = next((f for f in files if f.stem == day), files[-1] if files else None)
    cv_files = [config.CV_BASE_FILE, *(Path(p) for p in market_check.geo.S.get("cv", {}).get("files", []))]
    key = (path and path.name, path and path.stat().st_mtime,
           *(f.stat().st_mtime if f.exists() else 0 for f in cv_files))
    if key not in _market_cache:
        report = market_check.analyze(path.stem if path else "")
        report["advice"] = market_check.advice(report)
        _market_cache.clear()
        _market_cache[key] = report
    return _market_cache[key]


@app.get("/market", response_class=HTMLResponse, include_in_schema=False)
def market_page(request: Request, day: str = ""):
    return page(request, "market", r=market_report(day))


@app.get("/api/market", summary="Резюме против рынка",
         description="Локальный ATS-чекер: по подходящим вакансиям отчёта (state/jobs/<дата>.json) — какие "
                     "технологии требуются и где они в резюме (pdf / только в базе / нигде), как называются "
                     "вакансии и совпадает ли заголовок резюме, гигиена пунктов опыта и выводы.")
def api_market(day: str = ""):
    return market_report(day)


@app.get("/sources", response_class=HTMLResponse, include_in_schema=False)
def sources_page(request: Request):
    return page(request, "sources", **sources_payload())


# --- API ---------------------------------------------------------------------------------------

@app.get("/api/progress", summary="Состояние прогона",
         description="Содержимое state/progress.json: этапы, текущий источник, счётчики, лента событий, "
                     "ошибки; плюс eta_total (секунд до конца по прошлым прогонам) и busy. "
                     "Если прогонов ещё не было — null.")
def api_progress():
    return progress_payload()


@app.get("/api/progress/stream", summary="Поток состояния прогона (SSE)",
         description="Server-Sent Events: каждое изменение progress.json отправляется событием `progress` "
                     "(не чаще раза в секунду). Страница пайплайна слушает этот поток.")
async def api_progress_stream(request: Request):
    async def events():
        last = None
        while not await request.is_disconnected():
            data = progress_payload()
            text = json.dumps(data, ensure_ascii=False)
            if text != last:
                last = text
                yield f"event: progress\ndata: {text}\n\n"
            await asyncio.sleep(1)
    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@app.post("/api/runs", response_model=RunStarted, status_code=202, summary="Запустить прогон",
          description="Запускает скрипты в фоне (вывод — в last_run.log). 409, если прогон уже идёт, "
                      "в том числе запущенный из терминала или Планировщика.")
def api_run(body: RunRequest):
    return RunStarted(scripts=runner.start(body.kind, body.skip))


@app.post("/api/runs/current/skip", status_code=202, summary="Пропустить этап идущего прогона",
          description="Просит прогон остановить текущий этап: его цикл проверяет просьбу между шагами "
                      "(обычно в течение секунды-двух), собранное до этого идёт дальше. 409 — прогона нет, "
                      "этап уже не идёт или его нельзя пропустить.")
def api_skip(body: SkipRequest):
    data = progress.read()
    if not data or not progress.is_alive(data):
        raise HTTPException(409, "Прогон не идёт")
    stage = next((s for s in data["stages"] if s["key"] == body.stage), None)
    if not stage or stage["status"] != progress.RUNNING:
        raise HTTPException(409, "Этот этап сейчас не выполняется")
    if not stage.get("skippable"):
        raise HTTPException(409, "Этот этап нельзя пропустить на ходу")
    progress.request_skip(body.stage, data["pid"])
    return {"stage": body.stage}


@app.post("/api/runs/current/stop", status_code=202, summary="Остановить прогон целиком",
          description="Завершает идущий скрипт и не запускает следующие в очереди (прогон «всё»: почта, потом "
                      "вакансии). Собранное в этом прогоне теряется — отчёты остаются от прошлого. 409 — прогона нет.")
def api_stop():
    return {"pid": runner.stop()}


@app.get("/api/reports", summary="Список отчётов",
         description="Отчёты reports/jobs_YYYY-MM-DD.html, новые сверху. Открыть: /files/reports/<file>.")
def api_reports():
    return report_list()


@app.get("/api/sources", summary="Источники последнего прогона",
         description="По каждому источнику: вид (ats / aggregator / site / career_page), сколько вакансий "
                     "дал, ошибка; для ATS — платформа и slug. Плюс Telegram-каналы и запросы freehire.")
def api_sources():
    return sources_payload()


# --- резюме под вакансию -------------------------------------------------------------------------

class CvRequest(BaseModel):
    """Под какую вакансию собрать резюме: ссылка из отчёта, любая ссылка или вставленный текст."""
    url: str = Field("", description="Ссылка на вакансию. Сначала ищется в сохранённых отчётах (state/jobs/), "
                                     "иначе читается страница (robots.txt, 1 запрос/с)")
    date: str = Field("", description="Дата отчёта YYYY-MM-DD, из которого нажата кнопка — там ищется первой")
    text: str = Field("", description="Текст вакансии; если задан, ссылка не читается")
    company: str = Field("", description="Компания (уточняет или заменяет найденную)")
    title: str = Field("", description="Название вакансии (уточняет или заменяет найденное)")
    one_page: bool = Field(False, description="Вариант на одну страницу: ужимается, пока не влезет")


class CvPick(BaseModel):
    section: str = Field(description="Summary или компания из опыта")
    text: str
    pin: bool = Field(description="Пункт закреплён в базе (есть в любой версии)")
    techs: list[str] = Field(description="Технологии вакансии, найденные в пункте")
    topics: list[str] = Field(description="Темы вакансии, общие с пунктом")
    similarity: float = Field(description="Текстовая близость пункта к вакансии (TF-IDF, 0–1)")
    score: float = Field(description="Итоговый балл: технология 2, тема 1, близость до 4")


class CvResult(BaseModel):
    company: str
    title: str
    source: str = Field(description="Откуда взята вакансия: дата отчёта, page — со страницы, text — вставлена")
    pdf: str = Field(description="Ссылка на PDF")
    md: str = Field(description="Ссылка на Markdown-прототип (его можно поправить и пересобрать: cvgen.py --render)")
    pages: int
    one_page: bool
    fit: int = Field(description="Fit % резюме к вакансии")
    missing: list[str] = Field(description="Ключевые навыки вакансии, которых нет в резюме")
    techs: list[str]
    topics: list[str]
    picks: list[CvPick] = Field(description="Выбранные пункты и почему")


def resolve_job(body) -> dict:
    """Вакансия из запроса: вставленный текст, иначе по ссылке — из отчётов, иначе со страницы."""
    if body.text.strip():
        return {"url": body.url, "company": body.company, "title": body.title, "text": body.text, "found_in": "text"}
    if not body.url.strip():
        raise HTTPException(422, "Нужна ссылка на вакансию или её текст")
    job = vacancy_lookup.archived(body.url, body.date)
    if job is None:
        try:
            job = vacancy_lookup.fetch(body.url)
        except vacancy_lookup.VacancyNotFound as e:
            raise HTTPException(404, f"Вакансия не найдена: {e}")
    return job


@app.post("/api/cv", response_model=CvResult, summary="Собрать резюме под вакансию",
          description="Подбирает достижения, пункты опыта и навыки из cv/base.toml под текст вакансии "
                      "(технологии, темы, текстовая близость) и собирает PDF в cv_out/<дата>/. "
                      "Ничего не придумывает — только отбирает и переставляет. 404 — вакансию не нашли.")
def api_cv(body: CvRequest):
    job = resolve_job(body)
    company = body.company or job.get("company") or "Company"
    title = body.title or job.get("title") or "Vacancy"
    result = cvgen.build_cv(company, title, job["text"], job.get("url", ""), one_page=body.one_page)
    return CvResult(
        company=company, title=title, source=job.get("found_in", ""),
        pdf=f"/files/{result['pdf']}", md=f"/files/{result['md']}", pages=result["pages"],
        one_page=body.one_page, fit=result["fit"], missing=result["missing"], techs=result["techs"],
        topics=result["topics"], picks=result["picks"],
    )


class CoverRequest(CvRequest):
    style: Literal["short", "full"] = Field("short", description="short — тезисно, full — абзацами")


@app.post("/api/cover", summary="Сопроводительное письмо под вакансию",
          description="Из cv/base.toml ([summary] — достижения, [cover] — шаблон и soft skills) под текст вакансии; "
                      "ничего не придумывает. Сохраняется в cv_out/<дата>/…_cover_<стиль>.txt. "
                      "Вакансия — как у /api/cv (ссылка или текст).")
def api_cover(body: CoverRequest):
    job = resolve_job(body)
    result = cover_letter.build(body.company or job.get("company") or "Company",
                                body.title or job.get("title") or "Vacancy", job["text"], body.style)
    path = cover_letter.save(result, job.get("url", ""))
    return {**result, "source": job.get("found_in", ""), "file": f"/files/{path.relative_to(ROOT).as_posix()}"}


@app.get("/api/cv", summary="Собранные резюме", description="PDF из cv_out/, новые сверху (до 40).")
def api_cv_list():
    return recent_cvs()


# --- навыки профиля (балл релевантности) ------------------------------------------------------

def skills_payload() -> dict:
    """Навыки с правками + в скольких подходящих вакансиях последнего отчёта встречается каждый."""
    import re as _re

    rows = relevance.skill_table()
    files = sorted(config.JOBS_ARCHIVE_DIR.glob("*.json"))
    jobs = json.loads(files[-1].read_text(encoding="utf-8")) if files else []
    texts = [f"{j['title']}\n{j.get('text', '')}" for j in jobs if market_check.RELEVANT_TITLE_RX.search(j["title"])]
    for row in rows:
        try:
            rx = _re.compile(row["pattern"], _re.I)
            row["matches"] = sum(bool(rx.search(t)) for t in texts)
        except _re.error:
            row["matches"], row["invalid"] = 0, True
        row["share"] = round(100 * row["matches"] / len(texts)) if texts else 0
    return {"skills": rows, "relevant": len(texts), "day": files[-1].stem if files else ""}


class SkillChange(BaseModel):
    """Правка навыка: вес и / или выключение. Применится со следующего прогона."""
    weight: int | None = Field(None, ge=-30, le=30, description="Новый вес; отрицательный — штраф за навык в вакансии")
    disabled: bool | None = Field(None, description="true — навык не учитывается в балле")


class NewSkill(BaseModel):
    """Свой навык: имя, регулярное выражение (без учёта регистра) и вес."""
    name: str = Field(min_length=1, max_length=60)
    pattern: str = Field(min_length=1, description="Регулярное выражение, ищется в названии и тексте вакансии")
    weight: int = Field(2, ge=-30, le=30)


@app.get("/skills", response_class=HTMLResponse, include_in_schema=False)
def skills_page(request: Request):
    return page(request, "skills", **skills_payload())


@app.get("/api/skills", summary="Навыки профиля",
         description="Навыки из profile.toml с правками из profile_overrides.json и долей подходящих вакансий "
                     "последнего отчёта, где навык встречается.")
def api_skills():
    return skills_payload()


@app.put("/api/skills/{name}", summary="Изменить навык",
         description="Вес и / или выключение. Пишется в profile_overrides.json (profile.toml не меняется); "
                     "балл вакансий пересчитается при следующем прогоне.")
def api_skill_change(name: str, body: SkillChange):
    if not any(r["name"] == name for r in relevance.skill_table()):
        raise HTTPException(404, "Нет такого навыка")
    over = relevance.read_overrides()
    change = over["skills"].setdefault(name, {})
    if body.weight is not None:
        change["weight"] = body.weight
    if body.disabled is not None:
        change["disabled"] = body.disabled
    relevance.write_overrides(over)
    return next(r for r in relevance.skill_table() if r["name"] == name)


@app.post("/api/skills", status_code=201, summary="Добавить свой навык",
          description="Новый навык с регулярным выражением и весом (в profile_overrides.json). 422 — неверная "
                      "регулярка или такое имя уже есть.")
def api_skill_add(body: NewSkill):
    import re as _re
    try:
        _re.compile(body.pattern, _re.I)
    except _re.error as e:
        raise HTTPException(422, f"Неверное регулярное выражение: {e}")
    if any(r["name"].lower() == body.name.lower() for r in relevance.skill_table()):
        raise HTTPException(422, "Навык с таким именем уже есть")
    over = relevance.read_overrides()
    over["added"].append(body.model_dump())
    relevance.write_overrides(over)
    return body


@app.delete("/api/skills/{name}", summary="Сбросить правку или удалить свой навык",
            description="Свой навык удаляется; у навыка из profile.toml сбрасываются вес и выключение.")
def api_skill_reset(name: str):
    over = relevance.read_overrides()
    over["skills"].pop(name, None)
    over["added"] = [a for a in over["added"] if a["name"] != name]
    relevance.write_overrides(over)
    return {"name": name}


# --- события откликов ------------------------------------------------------------------------

class ApplicationEvent(BaseModel):
    """Событие отклика: пишется в applications_manual.toml, страница откликов пересобирается из кэша писем."""
    company: str = Field(min_length=1, description="Компания — как в отчёте, по ней событие склеится с письмами")
    kind: Literal["applied", "interview", "rejected", "offer", "other"] = Field(
        description="applied — отклик, interview — этап, rejected — отказ, offer — оффер, other — заметка")
    note: str = Field("", description="Что произошло")
    url: str = Field("", description="Ссылка на вакансию: в отчётах она будет помечена «🔁 уже откликался»")
    source: str = Field("", description="Откуда: сайт компании, LinkedIn, Telegram…")
    date: datetime | None = Field(None, description="Время события; по умолчанию сейчас")


@app.post("/api/applications/events", status_code=201, summary="Добавить событие отклика",
          description="Дописывает блок в applications_manual.toml и "
                      "пересобирает reports/applications.html из кэша писем — без запросов в Gmail.")
def api_application_event(body: ApplicationEvent):
    import applications
    try:
        event = manual_apps.add_event(body.company, body.kind, body.note, body.url, body.source, body.date)
    except ValueError as e:
        raise HTTPException(422, str(e))
    applications.build_page()
    return event


# --- подготовка к интервью ----------------------------------------------------------------------

@app.get("/prep", response_class=HTMLResponse, include_in_schema=False)
def prep_page(request: Request, url: str = "", date: str = ""):
    """Подготовка к интервью: с ?url= (кнопка «🎤» в отчёте) материалы собираются сразу."""
    result, error = None, ""
    if url:
        try:
            job = resolve_job(CvRequest(url=url, date=date))
            result = interview_prep.build(job.get("company") or "Company", job.get("title") or "Vacancy",
                                          job["text"], job.get("location", ""))
            result["url"] = url
        except HTTPException as e:
            error = e.detail
    return page(request, "prep", r=result, error=error, url=url)


@app.post("/prep", response_class=HTMLResponse, include_in_schema=False)
async def prep_page_text(request: Request):
    """Подготовка по вставленному тексту — для вакансий без ссылки (PDF от рекрутера, переписка).

    Форма разбирается вручную (urlencoded): python-multipart для неё не нужен."""
    from urllib.parse import parse_qs

    form = {k: v[0] for k, v in parse_qs((await request.body()).decode("utf-8")).items()}
    text = form.get("text", "").strip()
    result, error = None, ""
    if text:
        result = interview_prep.build(form.get("company", "").strip() or "Company",
                                      form.get("title", "").strip() or "Vacancy", text, form.get("location", "").strip())
        result["url"] = ""
    else:
        error = "Вставьте текст вакансии"
    return page(request, "prep", r=result, error=error, url="", form=form)


@app.post("/api/prep", summary="Подготовка к интервью",
          description="Из фактов cv/base.toml под вакансию: что из требований есть и чем это доказать, чего нет, "
                      "черновик рассказа о себе (EN), истории под поведенческие вопросы, чек-лист к HR, вопросы к "
                      "компании, медиана levels.fyi. Вакансия — как у /api/cv (ссылка или текст).")
def api_prep(body: CvRequest):
    job = resolve_job(body)
    return interview_prep.build(body.company or job.get("company") or "Company", body.title or job.get("title") or "Vacancy",
                                job["text"], job.get("location", ""))
