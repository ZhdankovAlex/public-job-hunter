import re

from . import config
from .models import Company, norm_name

_GENERIC_SUFFIXES = {"labs", "ai", "com", "io", "org", "group", "technologies", "tech", "inc", "hq", "app"}


def load_companies(path=config.COMPANIES_FILE):
    companies = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        name = parts[0]
        score = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        note = parts[2] if len(parts) > 2 else ""
        key = norm_name(name)
        # дубликаты (компания в двух группах списка) — оставляем больший приоритет
        if key not in companies or score > companies[key].score:
            companies[key] = Company(name, score, note)
    return list(companies.values())


_CYRILLIC_LOOKALIKES = str.maketrans("аеорсухкмтвнАЕОРСУХКМТВН", "aeopcyxkmtbhAEOPCYXKMTBH")


def slug_candidates(name):
    """Эвристические slug'и: 'Grafana Labs' -> grafanalabs, grafana-labs, grafana."""
    out = []
    base = re.sub(r"\(.*?\)", "", name)
    if re.search(r"[A-Za-z]", base):  # «Acme.аi» с кириллической «а»: кириллические двойники латинских букв
        base = base.translate(_CYRILLIC_LOOKALIKES)
    for variant in base.split("/"):
        tokens = re.findall(r"[a-z0-9]+", variant.lower())
        if not tokens:
            continue
        out.append("".join(tokens))
        if len(tokens) > 1:
            out.append("-".join(tokens))
            core = list(tokens)
            while len(core) > 1 and core[-1] in _GENERIC_SUFFIXES:
                core.pop()
            if core != tokens and len("".join(core)) >= 3:
                out.append("".join(core))
    return list(dict.fromkeys(out))[:5]
