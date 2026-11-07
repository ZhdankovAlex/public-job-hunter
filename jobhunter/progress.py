"""Прогресс прогона: этапы пайплайна, счётчики, текущий источник.

Скрипт (main.py, applications.py) объявляет этапы и отмечает шаги; состояние пишется в
state/progress.json не чаще раза в WRITE_EVERY секунд, а в терминале рисуется строка
с прогресс-баром. Смотреть прогресс из другого окна (например, когда прогон запущен
Планировщиком):

    .venv\\Scripts\\python.exe -m jobhunter.progress          # обновляется раз в секунду
    .venv\\Scripts\\python.exe -m jobhunter.progress --once   # один снимок

Модули (telegram.py, cvgen.py, …) вызывают функции модуля — step(), item_started() —
без передачи объекта: если прогона с прогрессом нет, вызовы ничего не делают.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sys
import threading
import time
from datetime import datetime

from . import config

PROGRESS_FILE = config.ROOT / "state" / "progress.json"
WRITE_EVERY = 0.5  # секунд между записями файла и перерисовкой строки
MAX_ERRORS = 2000  # предупреждений и ошибок прогона в progress.json (страховка от разрастания файла)

# Группы предупреждений — что с ними делать:
#   action — нужно действие человека: борд пропал/переехал, компания без борда, обрезано лимитом, не задан файл;
#   code   — проблема кода: формат ответа изменился, исключение (ERROR) — чинить в коде;
#   site   — сбой чужого сайта: robots.txt недоступен, HTTP 4xx/5xx, SSL, таймаут — обычно само проходит.
_ACTION_RX = re.compile(
    r"больше не существует|переехал|борда нет|slug не найден|открыто \d+ из \d+|не задан|не найдено —|"
    r"поправьте|settings\.toml|компания не определена", re.I)
_CODE_RX = re.compile(r"не удалось разобрать|формат .{0,40}изменился|traceback|exception", re.I)


def error_group(level: str, message: str) -> str:
    """action / code / site для предупреждения из лога."""
    if _ACTION_RX.search(message):
        return "action"
    if level in ("ERROR", "CRITICAL") or _CODE_RX.search(message):
        return "code"
    return "site"
MAX_EVENTS = 5000  # событий ленты за прогон (страховка от разрастания файла, как MAX_ERRORS)
HISTORY_FILE = config.ROOT / "state" / "progress_history.json"  # длительности этапов прошлых прогонов
HISTORY_RUNS = 5
# просьбы веб-интерфейса к идущему прогону: {"pid": …, "skip": [ключи этапов]}
CONTROL_FILE = config.ROOT / "state" / "progress_control.json"
CONTROL_EVERY = 1.0  # секунд между чтениями CONTROL_FILE
BAR_WIDTH = 24

PENDING, RUNNING, DONE, FAILED, SKIPPED = "pending", "running", "done", "failed", "skipped"
_ICONS = {PENDING: "·", RUNNING: "▶", DONE: "✔", FAILED: "✖", SKIPPED: "–"}

_current: "Pipeline | None" = None


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _bar(done: int, total: int) -> str:
    filled = int(BAR_WIDTH * done / total) if total else 0
    return "█" * filled + "░" * (BAR_WIDTH - filled)


def _duration(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 60}:{seconds % 60:02d}"


class Pipeline:
    """Состояние одного прогона.

    Attributes:
        script: имя скрипта («main», «applications»).
        stages: этапы по порядку: ключ, название, статус, done/total, время.
        counters: итоговые счётчики прогона (найдено, отброшено, …).
        errors: последние предупреждения и ошибки из лога.
    """

    def __init__(self, script: str, stages: list[tuple[str, str]], skippable: set[str] = frozenset()):
        """Объявляет этапы и делает этот прогон текущим.

        Args:
            script: имя скрипта для заголовка.
            stages: [(ключ, название)] в порядке выполнения.
            skippable: этапы, которые можно пропустить на ходу (кнопка в веб-интерфейсе):
                их циклы проверяют skip_requested() между шагами.
        """
        global _current
        self.script = script
        self.started = time.time()
        self.stages = [{"key": k, "title": t, "status": PENDING, "done": 0, "total": 0,
                        "started": None, "finished": None, "note": "", "skippable": k in skippable}
                       for k, t in stages]
        self._skip_keys: set[str] = set()
        self._control_read = 0.0
        try:  # просьбы прошлого прогона к этому не относятся
            CONTROL_FILE.unlink(missing_ok=True)
        except OSError:
            pass
        self.counters: dict[str, int] = {}
        self.tallies: dict[str, dict[str, int]] = {}  # разбивки: причины отказа, вакансии по источникам
        self.events: list[dict] = []  # лента прогона: что найдено, что отброшено (все этапы, до MAX_EVENTS)
        self.errors: list[dict] = []
        self.active: dict[str, float] = {}  # источники, которые обрабатываются прямо сейчас
        self.last_item = ""
        self.status = RUNNING
        self._lock = threading.RLock()
        self._written = 0.0
        self._tty = sys.stdout.isatty()
        _current = self
        logging.getLogger().addHandler(_ErrorCollector(self))
        self._flush(force=True)

    # --- этапы ------------------------------------------------------------------------------

    def _stage(self, key: str) -> dict:
        return next(s for s in self.stages if s["key"] == key)

    def _running(self) -> dict | None:
        return next((s for s in self.stages if s["status"] == RUNNING), None)

    def start(self, key: str, total: int = 0, note: str = "") -> None:
        """Начинает этап; предыдущий незавершённый этап считается выполненным."""
        with self._lock:
            prev = self._running()
            if prev:
                self._close(prev, SKIPPED if prev["key"] in self._skip_keys else DONE)
            stage = self._stage(key)
            stage.update(status=RUNNING, total=total, done=0, started=_now(), note=note)
            self.active.clear()
            self.last_item = ""
        self._flush(force=True)

    def skip(self, key: str, note: str = "") -> None:
        """Этап не выполняется (например, отключён флагом --no-telegram)."""
        with self._lock:
            self._stage(key).update(status=SKIPPED, note=note)
        self._flush(force=True)

    def skip_requested(self) -> bool:
        """Попросили ли из веб-интерфейса пропустить текущий этап (читает CONTROL_FILE не чаще раза в секунду).

        Цикл этапа, получив True, прекращает работу; собранное до этого идёт дальше по пайплайну.
        """
        with self._lock:
            stage = self._running()
            if stage is None or not stage["skippable"]:
                return False
            if stage["key"] in self._skip_keys:
                return True
            now = time.time()
            if now - self._control_read < CONTROL_EVERY:
                return False
            self._control_read = now
            try:
                control = json.loads(CONTROL_FILE.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return False
            if control.get("pid") != os.getpid() or stage["key"] not in control.get("skip", []):
                return False
            self._skip_keys.add(stage["key"])
            stage["note"] = f"пропущено вручную на {stage['done']}" + (f" из {stage['total']}" if stage["total"] else "")
        logging.getLogger(__name__).warning("Этап «%s» пропущен по кнопке в веб-интерфейсе", stage["title"])
        self.event("info", f"Этап «{stage['title']}» пропущен вручную")
        return True

    def _close(self, stage: dict, status: str) -> None:
        stage["status"], stage["finished"] = status, _now()
        if stage["total"] and status == DONE:
            stage["done"] = stage["total"]

    def set_total(self, total: int) -> None:
        """Сколько шагов у текущего этапа, если это стало известно после start()."""
        with self._lock:
            stage = self._running()
            if stage:
                stage["total"] = total
        self._flush()

    def step(self, item: str = "", n: int = 1) -> None:
        """Шаг текущего этапа; item — что только что обработано (компания, канал, письмо)."""
        with self._lock:
            stage = self._running()
            if stage:
                stage["done"] += n
            if item:
                self.last_item = item
        self._flush()

    def item_started(self, item: str) -> None:
        """Источник взят в работу (параллельная обработка — их может быть несколько)."""
        with self._lock:
            self.active[item] = time.time()
        self._flush()

    def item_finished(self, item: str) -> None:
        with self._lock:
            self.active.pop(item, None)
        self.step(item)

    def count(self, **values: int) -> None:
        """Итоговые счётчики: count(fetched=1200, matched=540)."""
        with self._lock:
            self.counters.update(values)
        self._flush()

    def tally(self, group: str, key: str, n: int = 1) -> None:
        """+n к разбивке group[key]: tally("rejects", "старше 30 дней")."""
        with self._lock:
            bucket = self.tallies.setdefault(group, {})
            bucket[key] = bucket.get(key, 0) + n
        self._flush()

    def event(self, kind: str, text: str) -> None:
        """Запись в ленту: kind — found / reject / source / mail / info."""
        with self._lock:
            self.events.append({"time": _now(), "kind": kind, "text": text[:300]})
            del self.events[:-MAX_EVENTS]
        self._flush()

    def finish(self, ok: bool = True) -> None:
        """Конец прогона: текущий этап закрывается, файл пишется последний раз."""
        global _current
        with self._lock:
            stage = self._running()
            if stage:
                self._close(stage, (SKIPPED if stage["key"] in self._skip_keys else DONE) if ok else FAILED)
            self.status = DONE if ok else FAILED
        self._flush(force=True)
        if ok:
            self._save_history()
        if self._tty:
            sys.stdout.write("\n")
            sys.stdout.flush()
        _current = None

    def _save_history(self) -> None:
        """Длительности этапов удачного прогона — для оценки «до конца прогона» в следующий раз."""
        durations = {}
        for s in self.stages:
            if s["started"] and s["finished"] and s["status"] == DONE:
                spent = datetime.fromisoformat(s["finished"]) - datetime.fromisoformat(s["started"])
                durations[s["key"]] = int(spent.total_seconds())
        history = read_history()
        runs = history.setdefault(self.script, [])
        runs.append({"finished": _now(), "total": int(time.time() - self.started), "stages": durations})
        del runs[:-HISTORY_RUNS]
        try:
            HISTORY_FILE.write_text(json.dumps(history, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass

    # --- вывод ------------------------------------------------------------------------------

    def snapshot(self) -> dict:
        """Состояние для progress.json: этапы, текущий источник, ETA, счётчики, ошибки."""
        with self._lock:
            stage = self._running()
            eta = None
            if stage and stage["total"] and stage["done"] and stage["started"]:
                spent = time.time() - datetime.fromisoformat(stage["started"]).timestamp()
                eta = int(spent / stage["done"] * (stage["total"] - stage["done"]))
            now = time.time()
            return {
                "script": self.script, "pid": os.getpid(), "status": self.status,
                "started": datetime.fromtimestamp(self.started).isoformat(timespec="seconds"),
                "updated": _now(), "elapsed": int(now - self.started),
                "stage": stage["key"] if stage else "", "eta_stage": eta,
                "last_item": self.last_item,
                "active": [{"item": k, "seconds": int(now - v)} for k, v in sorted(self.active.items(), key=lambda x: x[1])],
                "stages": [dict(s) for s in self.stages],
                "counters": dict(self.counters), "errors": list(self.errors),
                "tallies": {k: dict(v) for k, v in self.tallies.items()}, "events": list(self.events),
            }

    def _flush(self, force: bool = False) -> None:
        now = time.time()
        if not force and now - self._written < WRITE_EVERY:
            return
        self._written = now
        data = self.snapshot()
        PROGRESS_FILE.parent.mkdir(exist_ok=True)
        tmp = PROGRESS_FILE.with_suffix(".tmp")
        try:  # запись через временный файл: читатель не увидит половину JSON
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, PROGRESS_FILE)
        except OSError:
            pass  # файл открыт другим процессом (Windows) — запишем при следующем шаге
        if self._tty:
            self.draw()

    def draw(self) -> None:
        """Строка прогресса внизу терминала; лог печатается над ней (см. ConsoleHandler)."""
        sys.stdout.write("\r" + status_line(self.snapshot()).ljust(_width()) + "\r")
        sys.stdout.flush()


def _width() -> int:
    return shutil.get_terminal_size((100, 20)).columns - 1


def status_line(data: dict) -> str:
    """Одна строка: этап N/M, бар, done/total, ETA, текущий источник."""
    stages = data["stages"]
    stage = next((s for s in stages if s["key"] == data["stage"]), None)
    if stage is None:
        return f"[{data['script']}] {data['status']} · {_duration(data['elapsed'])}"
    number = stages.index(stage) + 1
    line = f"[{number}/{len(stages)}] {stage['title']}"
    if stage["total"]:
        pct = 100 * stage["done"] // stage["total"]
        line += f" {_bar(stage['done'], stage['total'])} {stage['done']}/{stage['total']} {pct}%"
    if data.get("eta_stage") is not None:
        line += f" · ~{_duration(data['eta_stage'])}"
    current = data["active"][-1]["item"] if data["active"] else data["last_item"]
    if current:
        line += f" · {current}"
    return line[: _width()]


def render(data: dict) -> str:
    """Многострочный вид пайплайна для `python -m jobhunter.progress`."""
    lines = [f"{data['script']}.py — {data['status']} · запущен {data['started'][11:]} · "
             f"прошло {_duration(data['elapsed'])} · обновлено {data['updated'][11:]}", ""]
    for n, s in enumerate(data["stages"], 1):
        line = f" {_ICONS[s['status']]} {n:>2}. {s['title']}"
        if s["total"]:
            line += f"  {_bar(s['done'], s['total'])} {s['done']}/{s['total']}"
        elif s["status"] == RUNNING and s["done"]:
            line += f"  {s['done']}"
        if s["started"] and s["finished"]:
            spent = datetime.fromisoformat(s["finished"]) - datetime.fromisoformat(s["started"])
            line += f"  ({_duration(spent.total_seconds())})"
        if s["note"]:
            line += f"  — {s['note']}"
        lines.append(line)
    if data["status"] == RUNNING:
        if data.get("eta_stage") is not None:
            lines += ["", f"До конца этапа ≈ {_duration(data['eta_stage'])}"]
        if data["active"]:
            lines += ["", "Сейчас обрабатываются:"]
            lines += [f"   {a['item']} ({a['seconds']} с)" for a in data["active"][:10]]
        elif data["last_item"]:
            lines += ["", f"Последний: {data['last_item']}"]
    if data["counters"]:
        lines += ["", "Счётчики: " + " · ".join(f"{k} {v}" for k, v in data["counters"].items())]
    if data["errors"]:
        lines += ["", f"Предупреждения ({len(data['errors'])}, последние):"]
        lines += [f"   {e['time'][11:]} {e['level']} {e['message'][:150]}" for e in data["errors"][-5:]]
    return "\n".join(lines)


CONTINUE_RUN_ENV = "JOBHUNTER_CONTINUE_RUN"  # второй скрипт того же прогона (почта -> вакансии): лог не ротировать


def rotate_errors_log() -> None:
    """Новый прогон: errors.log -> errors.prev.log (прошлый перезаписывается), errors.log начинается заново.

    Прогон «всё» — два скрипта подряд; второй запускается с JOBHUNTER_CONTINUE_RUN=1 и дописывает
    в тот же errors.log (веб-интерфейс и run_daily.bat ставят переменную сами).
    """
    import os

    from . import config

    if os.environ.get(CONTINUE_RUN_ENV) or not config.ERRORS_LOG.exists():
        return
    try:
        os.replace(config.ERRORS_LOG, config.ERRORS_LOG_PREV)
    except OSError:  # файл держит другой процесс — пишем дальше в старый
        pass


class _ErrorCollector(logging.Handler):
    """Предупреждения и ошибки из лога — в progress.json (последние MAX_ERRORS)."""

    def __init__(self, pipeline: Pipeline):
        super().__init__(logging.WARNING)
        self.pipeline = pipeline

    def emit(self, record: logging.LogRecord) -> None:
        p = self.pipeline
        if p is not _current:
            logging.getLogger().removeHandler(self)
            return
        with p._lock:
            message = record.getMessage()[:500]
            p.errors.append({"time": _now(), "level": record.levelname, "message": message,
                             "group": error_group(record.levelname, message)})
            del p.errors[:-MAX_ERRORS]


class ConsoleHandler(logging.StreamHandler):
    """Лог в терминал: стирает строку прогресса, печатает запись и рисует строку заново."""

    def emit(self, record: logging.LogRecord) -> None:
        p = _current
        if p is not None and p._tty:
            self.stream.write("\r" + " " * _width() + "\r")
        super().emit(record)
        if p is not None and p._tty:
            p.draw()


# --- функции модуля: работают с текущим прогоном или ничего не делают ----------------------

def start(key: str, total: int = 0, note: str = "") -> None:
    if _current:
        _current.start(key, total, note)


def skip(key: str, note: str = "") -> None:
    if _current:
        _current.skip(key, note)


def set_total(total: int) -> None:
    if _current:
        _current.set_total(total)


def step(item: str = "", n: int = 1) -> None:
    if _current:
        _current.step(item, n)


def item_started(item: str) -> None:
    if _current:
        _current.item_started(item)


def item_finished(item: str) -> None:
    if _current:
        _current.item_finished(item)


def count(**values: int) -> None:
    if _current:
        _current.count(**values)


def skip_requested() -> bool:
    """Просьба пропустить текущий этап; без активного прогона — False."""
    return bool(_current and _current.skip_requested())


def was_skipped(key: str) -> bool:
    """Был ли этап пропущен по кнопке в этом прогоне."""
    return bool(_current and key in _current._skip_keys)


def request_skip(stage: str, pid: int) -> None:
    """Для веб-интерфейса: попросить прогон с этим pid пропустить этап stage."""
    try:
        control = json.loads(CONTROL_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        control = {}
    if control.get("pid") != pid:
        control = {"pid": pid, "skip": []}
    if stage not in control["skip"]:
        control["skip"].append(stage)
    CONTROL_FILE.write_text(json.dumps(control), encoding="utf-8")


def tally(group: str, key: str, n: int = 1) -> None:
    if _current:
        _current.tally(group, key, n)


def event(kind: str, text: str) -> None:
    if _current:
        _current.event(kind, text)


def tracked(name: str, fn, *args):
    """Вызов fn(*args) с отметкой «источник в работе» — для пула потоков."""
    item_started(name)
    try:
        return fn(*args)
    finally:
        item_finished(name)


def read_history() -> dict:
    """{скрипт: [{finished, total, stages: {этап: секунды}}]} — последние HISTORY_RUNS прогонов."""
    try:
        return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def eta_total(data: dict) -> int | None:
    """Секунд до конца прогона: ETA текущего этапа + медиана прошлых прогонов для оставшихся этапов."""
    runs = read_history().get(data["script"], [])
    if not runs or data["status"] != RUNNING:
        return None
    left = 0
    for s in data["stages"]:
        past = sorted(r["stages"][s["key"]] for r in runs if s["key"] in r["stages"])
        typical = past[len(past) // 2] if past else 0
        if s["status"] == PENDING:
            left += typical
        elif s["status"] == RUNNING:
            if data.get("eta_stage") is not None:
                left += data["eta_stage"]
            elif s["started"]:
                spent = time.time() - datetime.fromisoformat(s["started"]).timestamp()
                left += max(typical - int(spent), 0)
    return left


def is_alive(data: dict) -> bool:
    """Идёт ли прогон из progress.json на самом деле (процесс жив)."""
    return data.get("status") == RUNNING and _alive(data["pid"])


def read() -> dict | None:
    """Последнее состояние из progress.json или None, если прогонов ещё не было."""
    try:
        return json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _alive(pid: int) -> bool:
    """Жив ли процесс прогона: упавший скрипт не успевает записать статус failed."""
    if sys.platform == "win32":
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def watch(once: bool = False) -> None:
    """Перерисовывает пайплайн из progress.json раз в секунду до конца прогона."""
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    while True:
        data = read()
        if data is None:
            print("Прогонов ещё не было: state/progress.json нет.")
            return
        if data["status"] == RUNNING and not _alive(data["pid"]):
            data["status"] = "прервано (процесс завершился без отметки о конце)"
        text = render(data)
        if once or data["status"] != RUNNING:
            print(text)
            return
        os.system("cls" if sys.platform == "win32" else "clear")
        print(text)
        time.sleep(1)


if __name__ == "__main__":
    watch(once="--once" in sys.argv)
