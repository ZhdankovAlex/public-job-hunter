"""HTTP-клиент: не чаще 1 запроса в секунду на хост + проверка robots.txt."""
import logging
import re
import threading
import time
from urllib.parse import urlsplit

import requests

from . import config

log = logging.getLogger(__name__)

# «Пропустить этап» на источниках: запросы недоделанных источников обрываются сразу, а не после
# их долгого обхода (карьерная страница — до 100 вакансий по секунде). Ставит и снимает main.py.
cancel = threading.Event()


class Cancelled(BaseException):
    """Запрос отменён кнопкой. BaseException — чтобы не ловился как ошибка источника (except FetchError /
    except Exception): источник просто выходит, не помечая борд сломанным. Ловит пул потоков (future)."""


class FetchError(Exception):
    pass


class RobotsDisallowed(FetchError):
    pass


class _Robots:
    """Минимальный парсер robots.txt с поддержкой * и $ (urllib их не умеет)."""

    def __init__(self, text, ua_token):
        self.rules = []  # (allow: bool, pattern: str)
        self.crawl_delay = 0.0
        groups, agents, body, in_agents = {}, [], [], False
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            key, value = (p.strip() for p in line.split(":", 1))
            key = key.lower()
            if key == "user-agent":
                if not in_agents:
                    agents, body = [], []
                agents.append(value.lower())
                groups[value.lower()] = body
                in_agents = True
            else:
                in_agents = False
                body.append((key, value))
        group = next((g for a, g in groups.items() if a != "*" and a in ua_token), None)
        if group is None:
            group = groups.get("*", [])
        for key, value in group:
            if key in ("allow", "disallow") and value:
                self.rules.append((key == "allow", value))
            elif key == "crawl-delay":
                try:
                    self.crawl_delay = float(value)
                except ValueError:
                    pass

    @staticmethod
    def _matches(pattern, path):
        anchored = pattern.endswith("$")
        rx = ".*".join(re.escape(p) for p in pattern.rstrip("$").split("*"))
        return re.match(rx + ("$" if anchored else ""), path) is not None

    def allowed(self, path):
        best = (0, True)  # самое длинное правило побеждает, при равенстве — allow
        for allow, pattern in self.rules:
            if self._matches(pattern, path):
                best = max(best, (len(pattern), allow))
        return best[1]


class Http:
    def __init__(self, min_interval=config.MIN_INTERVAL):
        self.min_interval = min_interval
        self._local = threading.local()
        self._guard = threading.Lock()
        self._host_locks = {}
        self._next_slot = {}
        self._robots = {}
        self._rate_limited = {}  # host -> сколько запросов подряд закончились 429

    def _session(self):
        if not hasattr(self._local, "session"):
            s = requests.Session()
            s.headers["User-Agent"] = config.USER_AGENT
            s.headers["Accept"] = "application/json"
            self._local.session = s
        return self._local.session

    def _host_lock(self, host):
        with self._guard:
            return self._host_locks.setdefault(host, threading.Lock())

    def _throttle(self, host):
        with self._host_lock(host):
            delay = self._next_slot.get(host, 0) - time.monotonic()
            if delay > 0 and cancel.wait(delay):
                raise Cancelled(host)
            robots = self._robots.get(host)
            interval = max(
                config.HOST_INTERVALS.get(host, self.min_interval),
                robots.crawl_delay if robots else 0,
            )
            self._next_slot[host] = time.monotonic() + interval

    def _load_robots(self, scheme, host):
        r = None
        for _ in range(2):  # одна повторная попытка при сетевом сбое
            self._throttle(host)
            try:
                r = self._session().get(f"{scheme}://{host}/robots.txt", timeout=20)
                break
            except requests.RequestException as e:
                error = e
        if r is None:
            log.warning("robots.txt недоступен для %s (%s) — хост пропущен", host, error)
            return None
        if r.status_code == 200:
            return _Robots(r.text, config.USER_AGENT.lower())
        if 400 <= r.status_code < 500:
            return _Robots("", "")  # нет robots.txt — ограничений нет (RFC 9309)
        log.warning("robots.txt %s вернул %s — хост пропущен", host, r.status_code)
        return None

    def _check_robots(self, url):
        parts = urlsplit(url)
        host = parts.netloc
        with self._guard:
            known = host in self._robots
        if not known:
            robots = self._load_robots(parts.scheme, host)
            with self._guard:
                self._robots.setdefault(host, robots)
        robots = self._robots[host]
        path = parts.path + ("?" + parts.query if parts.query else "")
        if robots is None or not robots.allowed(path):
            raise RobotsDisallowed(f"robots.txt запрещает {url}")

    def get(self, url, params=None, timeout=40, headers=None):
        """GET с лимитом и повторами. Возвращает requests.Response (в т.ч. 404).

        headers — дополнительные заголовки (например, Accept: text/html для сайтов, которые на JSON отвечают 404).
        """
        return self._request("GET", url, params=params, timeout=timeout, headers=headers)

    def post(self, url, json=None, timeout=40, headers=None):
        """POST с JSON-телом — поиск вакансий, который сайт делает POST-запросом (Workday).

        Те же robots.txt, лимит частоты и повторы, что у get(); ничего не отправляет, кроме поискового запроса.
        """
        return self._request("POST", url, json=json, timeout=timeout, headers=headers)

    def _request(self, method, url, params=None, json=None, timeout=40, headers=None):
        url = requests.Request(method, url, params=params).prepare().url
        self._check_robots(url)
        host = urlsplit(url).netloc
        if self._rate_limited.get(host, 0) >= 3:
            # хост упорно отвечает 429 — до конца запуска его не трогаем
            raise FetchError(f"{url}: хост отключён до следующего запуска (429)")
        last = None
        for attempt in range(3):
            if cancel.is_set():
                raise Cancelled(url)
            self._throttle(host)
            try:
                r = self._session().request(method, url, json=json, timeout=timeout, headers=headers)
            except requests.RequestException as e:
                last = str(e)
                continue
            if r.status_code == 429 or r.status_code >= 500:
                last = f"HTTP {r.status_code}"
                retry_after = r.headers.get("Retry-After", "")
                wait = int(retry_after) if retry_after.isdigit() else 5 * (attempt + 1)
                if cancel.wait(min(wait, 60)):
                    raise Cancelled(url)
                continue
            self._rate_limited[host] = 0
            return r
        if last == "HTTP 429":
            self._rate_limited[host] = self._rate_limited.get(host, 0) + 1
        raise FetchError(f"{url}: {last}")
