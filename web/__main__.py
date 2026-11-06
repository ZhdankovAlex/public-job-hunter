"""python -m web — локальный сервер интерфейса на 127.0.0.1:8765 и вкладка в браузере."""
import argparse
import os
import threading
import webbrowser

import uvicorn

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--port", type=int, default=8765)
# в контейнере — 0.0.0.0 (наружу порт публикуется только на 127.0.0.1, см. docker-compose.yml)
parser.add_argument("--host", default=os.environ.get("JOBHUNTER_HOST", "127.0.0.1"))
parser.add_argument("--no-browser", action="store_true", help="не открывать браузер")
args = parser.parse_args()
if not args.no_browser:
    threading.Timer(1.5, webbrowser.open, [f"http://127.0.0.1:{args.port}/"]).start()
# только локальный адрес: в отчётах почта, отклики и контакты
uvicorn.run("web.app:app", host=args.host, port=args.port, log_level="warning")
