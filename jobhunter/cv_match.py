"""Сопоставление вакансии с резюме: что из требований есть, чего не хватает, где несоответствие.

Резюме (PDF) указываются в settings.toml ([cv].files). Из их текста по словарю технологий
собирается «что есть у кандидата»; из текста вакансии — «что требуют». Технологии из
nice-to-have не считаются пробелом, как и альтернативы в перечислениях вида «X, Y or Z»,
если одна из них есть в резюме.
"""
from __future__ import annotations

import json
from collections import Counter
import logging
import re
from pathlib import Path

from . import config

log = logging.getLogger(__name__)

CACHE_FILE = config.ROOT / "state" / "cv_text.json"
_I = re.I

# Технология -> шаблон. Имена — так они показываются в отчёте.
TECH = {
    # языки
    "Python": r"\bpython\b", "Go": r"\b[Gg]olang\b|\bGo\b(?=\s*(?:,|/|\)|and\b|or\b|developer|engineer|services?|experience))",
    "Java": r"\bjava\b(?!\s*script)", "Kotlin": r"\bkotlin\b", "Scala": r"\bscala\b", "Rust": r"\brust\b",
    "C++": r"c\+\+", ".NET / C#": r"(?<![\w])\.net\b|\bc#", "TypeScript": r"\btypescript\b",
    "JavaScript": r"\bjavascript\b", "Node.js": r"\bnode(?:\.?js)?\b", "Ruby": r"\bruby\b|\brails\b",
    "PHP": r"\bphp\b", "Elixir": r"\belixir\b", "SQL": r"\bsql\b",
    # фреймворки и протоколы
    "Django": r"\bdjango\b", "FastAPI": r"fastapi", "Flask": r"\bflask\b", "Tornado": r"\btornado\b",
    "SQLAlchemy": r"sqlalchemy", "Celery": r"\bcelery\b", "asyncio": r"asyncio|aiohttp|async python",
    "Pydantic": r"pydantic", "gRPC": r"\bgrpc\b", "GraphQL": r"graphql", "REST API": r"\brest(?:ful)?\b",
    "WebSocket": r"websockets?", "pytest": r"pytest",
    "Jira": r"\bjira\b", "Agile / Scrum": r"\bagile\b|\bscrum\b|\bkanban\b",
    # общие понятия, которые часто стоят в требованиях
    "Distributed systems": r"distributed systems?", "Microservices": r"micro[\s-]?services?",
    "Event-driven architecture": r"event[\s-]+driven", "Observability": r"observability",
    "Encryption": r"encrypt\w*|cryptograph\w*", "NoSQL": r"\bnosql\b", "Git": r"\bgit\b(?![\s-]?(?:lab|hub))",
    "Bash": r"\bbash\b|shell scripting",
    # хранилища
    "PostgreSQL": r"postgres(?:ql)?", "MySQL": r"\bmysql\b", "MongoDB": r"mongo(?:db)?", "Redis": r"\bredis\b",
    "Cassandra": r"cassandra", "ClickHouse": r"clickhouse", "Elasticsearch / OpenSearch": r"elastic\s?search|opensearch",
    "DynamoDB": r"dynamodb", "SQLite": r"sqlite", "Snowflake": r"\bsnowflake\b", "BigQuery": r"bigquery",
    # очереди
    "Kafka": r"\bkafka\b", "RabbitMQ": r"rabbitmq", "SQS / SNS": r"\bsqs\b|\bsns\b", "NATS": r"\bnats\b",
    # инфраструктура
    "Docker": r"\bdocker\b", "Kubernetes": r"kubernetes|\bk8s\b", "Helm": r"\bhelm\b", "Terraform": r"terraform",
    "Ansible": r"ansible", "AWS": r"\baws\b|amazon web services", "GCP": r"\bgcp\b|google cloud",
    "Azure": r"\bazure\b", "Linux": r"\blinux\b", "CI/CD": r"ci/cd|gitlab ci|github actions|jenkins|circleci",
    "Prometheus": r"prometheus", "Grafana": r"grafana", "OpenTelemetry": r"opentelemetry|otel\b",
    "Datadog": r"datadog",
    # данные
    "Airflow": r"airflow", "Spark": r"\bspark\b", "dbt": r"\bdbt\b", "Flink": r"\bflink\b",
    "ETL": r"\betl\b|\belt\b pipelines?", "Databricks": r"databricks", "Pandas": r"\bpandas\b", "Hadoop": r"hadoop",
    "Redshift": r"redshift", "Data warehouse": r"\bdwh\b|data warehous\w*", "BI tools": r"\bbi\b|tableau|looker|power bi",
    "MLOps": r"\bmlops\b", "Machine learning": r"machine learning", "NLP": r"\bnlp\b|natural language processing",
    "MCP": r"\bmcp\b|model context protocol", "GPU / CUDA": r"\bgpus?\b|\bcuda\b",
    "ELK": r"\belk\b|kibana|logstash", "Argo CD / Workflows": r"\bargo\s?(?:cd|workflows?)?\b",
    "Networking (TCP/IP, DNS)": r"\btcp(?:/ip)?\b|\bdns\b|\bbgp\b|network protocols",
    "HTTP": r"\bhttps?\b(?!s?:)",
    "IAM": r"\biam\b|identity and access", "Security (SIEM, SOC)": r"\bsiem\b|\bsoc ?2?\b", "GDPR": r"\bgdpr\b",
    "Perl": r"\bperl\b", "ERP": r"\berp\b", "ORM": r"\borms?\b",
    # AI
    "LLM": r"\bllms?\b|large language models?", "RAG": r"\brag\b|retrieval[\s-]augmented",
    "Vector search / embeddings": r"embeddings?|vector (?:search|database|store|db)|pgvector|chromadb|pinecone|weaviate|qdrant",
    "LangChain / LlamaIndex": r"langchain|llamaindex|langgraph", "PyTorch / TensorFlow": r"pytorch|tensorflow",
    "AI coding agents": r"claude code|cursor\b|copilot|windsurf|ai[\s-]assisted (?:development|coding)|coding agents?",
    "LLM evaluation": r"\bevals?\b|evaluation (?:of|for) (?:llm|retrieval|rag)|hit@|\bmrr\b|ndcg",
    # фронтенд
    "React": r"\breact\b(?!\s*native)", "Vue": r"\bvue(?:\.js)?\b", "Angular": r"\bangular\b",
}
TECH_RX = {name: re.compile(rx, 0 if name == "Go" else _I) for name, rx in TECH.items()}

# Что в резюме написано другими словами
CV_ALIASES = {
    "CI/CD": r"gitlab ci|github actions|jenkins", "Vector search / embeddings": r"chromadb|sentence-transformers|embeddings",
    "LLM": r"\bllms?\b|ollama|rag", "AWS": r"\bs3\b", "AI coding agents": r"claude code|windsurf",
    "LLM evaluation": r"hit@|mrr|retrieval evaluation",
    "asyncio": r"tornado|fastapi|aiohttp|websocket",  # асинхронные фреймворки = опыт asyncio
    "Pydantic": r"fastapi",  # модели FastAPI — это Pydantic
    "Observability": r"prometheus|grafana", "NoSQL": r"\bredis\b", "Git": r"gitlab|github",
    "ORM": r"sqlalchemy", "HTTP": r"rest(?:ful)? api|websocket",
}

_NICE_RX = re.compile(
    r"nice[\s-]to[\s-]have|bonus|\ba plus\b|is a plus|preferred|desirable|advantage|optional|familiarity|"
    r"exposure to|good to have|would be great|ideally|будет плюсом|желательно|приветствуется", _I)
_ALTERNATIVES_RX = re.compile(r"\bor\b|\be\.g\.|such as|similar|equivalent|like\b|/|или|аналог", _I)
_CLAUSE_SPLIT_RX = re.compile(r"\band\b(?!\s*/)|\bas well as\b|\bplus\b|;| и ", re.I)
_SENTENCE_RX = re.compile(r"[^\n.!?;•]+")
_ENGLISH_HIGH_RX = re.compile(r"\b(?:c1|c2)\b[^.\n]{0,25}english|english[^.\n]{0,25}\b(?:c1|c2|native|mother tongue)\b|"
                              r"native[\s-]level english|native english", _I)
_DEGREE_RX = re.compile(r"(?:master'?s|msc|m\.sc|phd|ph\.d)[^.\n]{0,40}(?:required|must)|"
                        r"(?:required|must have)[^.\n]{0,40}(?:master'?s|msc|phd)", _I)
# AI-резюме — если AI/ML в названии роли или LLM/RAG действительно суть вакансии (несколько упоминаний)
_AI_TITLE_RX = re.compile(r"\bai\b|\bml\b|\bllms?\b|machine learning|genai|agentic", _I)
_AI_TEXT_RX = re.compile(r"\bllms?\b|\brag\b|retrieval[\s-]augmented|embeddings?|vector (?:search|database|store)|"
                         r"langchain|llamaindex|prompt engineering|ai agents?", _I)
# О кандидате — settings.toml [cv]: лет опыта (0 — не сравнивать), уровень английского, есть ли магистр / PhD
def _cv_setting(key, default):
    from . import geo  # geo читает settings.toml при загрузке

    return geo.S.get("cv", {}).get(key, default)


CV_YEARS = int(_cv_setting("years_experience", 0))
CV_ENGLISH = str(_cv_setting("english_level", "")).upper()
CV_HAS_DEGREE = bool(_cv_setting("has_masters_or_phd", False))
# навыки, которые есть, но частично (settings.toml [cv.partial]: AWS = "только S3") — не пробел, а оговорка
PARTIAL = dict(_cv_setting("partial", {}))
# Fit %: доля закрытых ключевых навыков весит 85%, nice-to-have — 15%; несоответствия и частичные
# совпадения снижают итог
FIT_CORE_WEIGHT = 0.85
FIT_MISMATCH_PENALTY = 15
FIT_PARTIAL_PENALTY = 3
FIT_MAX_WEIGHT = 10  # ключевой навык весит по числу упоминаний в тексте, но не больше этого
# Термины вне словаря (Odoo, ERP, Temporal…): слово с заглавной буквы в середине предложения,
# которое встречается в тексте от UNKNOWN_MIN_MENTIONS раз или стоит в названии вакансии
UNKNOWN_MIN_MENTIONS = 3
UNKNOWN_MAX_WEIGHT = 5  # термин вне словаря не перевешивает проверенные технологии
# строка-требование: термин в ней — навык, даже если упомянут один раз
_REQUIREMENT_LINE_RX = re.compile(
    r"experience (?:with|in|using|of)|knowledge of|proficien\w*|familiar\w*|hands[\s-]on|expertise|"
    r"strong (?:skills|background|understanding)|understanding of|worked with|background in|"
    r"опыт (?:работы )?(?:с|в)|знани\w+|владени\w+|умени\w+ работать",
    re.I,
)
# шаблонные строки, где заглавные слова — не навыки: зарплата, бенефиты, equal opportunity
_BOILERPLATE_RX = re.compile(
    r"equal opportunit|fair chance|affirmative action|disabilit|veteran|gender identity|national origin|"
    r"pay range|salary range|base (?:pay|salary)|compensation|annually|per (?:year|annum)|\bUSD\b|\bEUR\b|"
    r"benefits|health (?:insurance|care)|401\(?k|paid time off|parental leave|stock options?|equity",
    re.I,
)
_MIDSENTENCE_TERM_RX = re.compile(r"(?<=[a-z0-9,;:(/] )([A-Z][A-Za-z0-9+#]{1,30}|[A-Z]{2,}[a-z]?)(?=[\s.,;:)/-]|$)")
_TITLE_TERM_RX = re.compile(r"\b([A-Z][A-Za-z0-9+#]{1,30})\b")
# слова, которые пишут с заглавной, но это не технологии и не навыки
_GENERIC_TERMS = set("""
software engineer engineers engineering developer developers development backend frontend fullstack senior junior
middle lead principal staff head team teams group company product products platform platforms services service
solutions solution systems system best place work master masters bachelor degree computer science information
technology technologies technical functional certification certified english russian spanish german europe european
remote office hybrid inc ltd llc gmbh we you our the and data cloud business financial finance global international
applications application enterprise management manager managers operations customer customers partner partners client
clients mobile web api apis ui ux ceo cto cpo coo hr it qa ci cd sre saas b2b b2c faq eu us uk usa emea gmt utc cet
pto ai ml monday tuesday wednesday thursday friday january february march april may june july august september
october november december key core new about who what why how join apply benefits requirements responsibilities
mission vision values equal opportunity employer diversity inclusion health insurance salary bonus equity stock
fintech igaming crypto trading market markets marketing sales legal compliance security research design
architecture scientist science analyst analytics interview interviews case cases assurance integration
integrations stack officer specialist executive relationship people brand reporting acquisition risk payments
events creative producer designer counsel safety construction balance did north south east west african indian
asian american china india familiarity proven machine learning generative excellent ability
amsterdam america americas nasdaq israel spain london berlin paris cyprus limassol poland warsaw
portugal lisbon netherlands germany france canada brazil mexico singapore dubai uae tbilisi georgia armenia
yerevan kazakhstan almaty ireland dublin switzerland zurich sweden stockholm estonia tallinn lithuania vilnius
google microsoft apple amazon meta facebook venmo paypal openai anthropic nvidia ads netflix uber airbnb stripe
ii iii full power id gtm salesforce codex stem phd msc bsc
""".split())


def _pdf_text(path: Path) -> str:
    from pypdf import PdfReader

    return "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)


class CvProfile:
    """Навыки из резюме и сопоставление с текстом вакансии."""

    def __init__(self, settings: dict) -> None:
        """settings — секция [cv] из settings.toml: files = [пути к PDF], names = [подписи]."""
        cache = json.loads(CACHE_FILE.read_text(encoding="utf-8")) if CACHE_FILE.exists() else {}
        self.cvs: list[tuple[str, str]] = []
        names = settings.get("names") or []
        for i, raw in enumerate(settings.get("files") or []):
            path = Path(raw)
            if not path.is_absolute():  # «cv/My_Backend_CV.pdf» — от корня проекта (так и в контейнере)
                path = config.ROOT / path
            label = names[i] if i < len(names) else path.stem
            key = f"{path}|{path.stat().st_mtime if path.exists() else 0}"
            if key not in cache:
                if not path.exists():
                    log.warning("Резюме %s не найдено — сопоставление по нему пропущено", path)
                    continue
                cache[key] = _pdf_text(path)
            self.cvs.append((label, cache[key]))
        CACHE_FILE.parent.mkdir(exist_ok=True)
        CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        text = "\n".join(t for _, t in self.cvs)
        # база для генератора резюме (cv/base.toml) — тоже правда о навыках, даже если её ещё нет в PDF
        base = config.CV_BASE_FILE
        if base.exists():
            text += "\n" + base.read_text(encoding="utf-8")
        self.cv_text = text.lower()
        self.have = {name for name, rx in TECH_RX.items() if rx.search(text)}
        self.have |= {name for name, rx in CV_ALIASES.items() if re.search(rx, text, _I)}

    def _unknown_terms(self, job, text: str) -> dict[str, int]:
        """Технические термины вне словаря, которых нет в резюме: {термин: сколько раз в тексте}."""
        known = {n.lower() for n in TECH_RX} | {n.lower() for n in CV_ALIASES}
        company = (getattr(job, "company", "") or "").lower()
        # шаблонные строки (зарплата, бенефиты, equal opportunity) — не про навыки
        lines = [line for line in text.splitlines() if not _BOILERPLATE_RX.search(line)]
        text = "\n".join(lines)
        mid = Counter(_norm_term(m) for m in _MIDSENTENCE_TERM_RX.findall(text))
        # строки-требования: «Experience with Temporal» — одного упоминания достаточно
        required = {_norm_term(m) for line in lines if _REQUIREMENT_LINE_RX.search(line)
                    for m in _MIDSENTENCE_TERM_RX.findall(line)}
        title_terms = {_norm_term(m) for m in _TITLE_TERM_RX.findall(job.title)}
        # для проверки «пишется ли со строчной» — без ссылок и доменов («odoo.sh», «github.com/…»)
        plain = re.sub(r"\S+\.(?:sh|com|io|ai|org|net|dev|app)\S*|https?://\S+", " ", text)
        found = {}
        for term in set(mid) | title_terms:
            low = term.lower()
            if (len(term) < 2 or low in _GENERIC_TERMS or low in known
                    or re.search(rf"(?<![a-z0-9]){re.escape(low)}(?![a-z0-9])", self.cv_text)
                    or (company and (low in company or company.startswith(low)))
                    or any(rx.search(term) for rx in TECH_RX.values())):
                continue
            # обычное слово хоть раз пишется со строчной («learning», «range», «pay»), название — никогда
            if term != low and re.search(rf"(?<![\w-]){re.escape(low)}(?![\w])", plain):
                continue
            # подпись поля: «Location: Spain», «Salary: …»
            if re.search(rf"(?<![\w-]){re.escape(term)}\s*:", text):
                continue
            # ключевой, если: стоит в строке-требовании; или часто мелькает (вакансия «про Odoo»);
            # или в названии вакансии («Backend Engineer, Odoo») и упомянут в тексте
            if term in required or mid[term] >= UNKNOWN_MIN_MENTIONS or (term in title_terms and term in text):
                found[term] = min(UNKNOWN_MAX_WEIGHT, max(1, mid[term]))
        return found

    def analyze(self, job, text: str) -> None:
        """Заполняет job.cv_have / cv_missing / cv_mismatch / cv_full / cv_pick и Fit % с разбивкой
        на ключевые навыки и nice-to-have (job.cv_fit, cv_core_*, cv_nice_*)."""
        from . import verify

        have, missing = set(), []
        core_have, core_missing, nice_have, nice_missing = set(), set(), set(), set()
        for m in _SENTENCE_RX.finditer(f"{job.title}\n{text}"):
            sentence = m.group()
            hits = [name for name, rx in TECH_RX.items() if rx.search(sentence)]
            if not hits:
                continue
            # части предложения: «frontend (React, JS) and backend, ideally Python (C++ or Java)» — «ideally»
            # и «or» относятся только к своей части, React здесь обязателен
            cuts = [0, *(m.end() for m in _CLAUSE_SPLIT_RX.finditer(sentence)), len(sentence) + 1]
            clauses = [sentence[a:b] for a, b in zip(cuts, cuts[1:])]
            # «Nice to have: Go, Rust and Kafka» — оговорка в начале предложения действует на всё предложение
            header_nice = bool(_NICE_RX.search(sentence[:40]))

            def clause_of(name):
                m = TECH_RX[name].search(sentence)
                pos = m.start() if m else 0
                return next(i for i, (a, b) in enumerate(zip(cuts, cuts[1:])) if a <= pos < b)

            by_clause = {}
            for name in hits:
                by_clause.setdefault(clause_of(name), []).append(name)
            for idx, names in by_clause.items():
                clause = clauses[idx]
                nice = header_nice or bool(_NICE_RX.search(clause))
                # «Python, Go or Java» — достаточно одного из перечисленных в этой части предложения
                any_of = bool(_ALTERNATIVES_RX.search(clause)) and any(h in self.have for h in names)
                for name in names:
                    if name in self.have:
                        have.add(name)
                        (nice_have if nice else core_have).add(name)
                    elif any_of:
                        continue
                    else:
                        (nice_missing if nice else core_missing).add(name)
                        if not nice and name not in missing:
                            missing.append(name)
        # навык, который где-то требуется без оговорок, — ключевой, даже если ещё раз упомянут как плюс
        nice_have -= core_have
        nice_missing -= core_missing
        full = f"{job.title}\n{text}"
        weights = {name: len(TECH_RX[name].findall(full)) for name in core_have | core_missing}
        for term, count in self._unknown_terms(job, text).items():
            core_missing.add(term)
            weights[term] = count
            if term not in missing:
                missing.append(term)
        mismatch = []
        years = verify._years(text)
        if CV_YEARS and years and years[0] > CV_YEARS:
            mismatch.append(f"опыт от {years[0]} лет (в резюме {CV_YEARS})")
        if not CV_HAS_DEGREE and _DEGREE_RX.search(text):
            mismatch.append("нужен магистр / PhD")
        if getattr(job, "mismatch_lang", ""):
            mismatch.append(f"основной язык — {job.mismatch_lang}")
        job.cv_have = sorted(have)
        job.cv_missing = missing[:6]
        job.cv_mismatch = mismatch
        job.cv_partial = [f"{n}: {PARTIAL[n]}" for n in sorted(have) if n in PARTIAL]
        # английский ниже C1 в резюме — не штраф, а оговорка
        if CV_ENGLISH and CV_ENGLISH < "C1" and _ENGLISH_HIGH_RX.search(text):
            job.cv_partial.append(f"просят English C1+ (в резюме {CV_ENGLISH})")
        job.cv_full = bool(have) and not missing and not mismatch
        job.cv_core_have, job.cv_core_missing = sorted(core_have), sorted(core_missing)
        job.cv_nice_have, job.cv_nice_missing = sorted(nice_have), sorted(nice_missing)
        job.cv_fit = fit_percent(core_have, core_missing, nice_have, nice_missing, len(mismatch), len(job.cv_partial),
                                 weights)
        ai = len(self.cvs) > 1 and (_AI_TITLE_RX.search(job.title) or len(_AI_TEXT_RX.findall(text)) >= 3)
        job.cv_pick = self.cvs[0][0] if ai or len(self.cvs) == 1 else (self.cvs[1][0] if self.cvs else "")


def fit_percent(core_have, core_missing, nice_have, nice_missing, mismatches: int, partial: int,
                weights: dict | None = None) -> int:
    """Fit % вакансии и резюме; -1 — в тексте не нашлось ни одной технологии.

    Ключевой навык весит по числу упоминаний (1…FIT_MAX_WEIGHT): вакансия, где Odoo в каждом
    предложении, а Python упомянут раз, с резюме без Odoo совпадает слабо.
    """
    if not (core_have or core_missing or nice_have or nice_missing):
        return -1
    weights = weights or {}

    def w(name):
        return min(FIT_MAX_WEIGHT, max(1, weights.get(name, 1)))

    core_total = sum(w(n) for n in core_have) + sum(w(n) for n in core_missing)
    nice_total = len(nice_have) + len(nice_missing)
    core = sum(w(n) for n in core_have) / core_total if core_total else 1.0
    nice = len(nice_have) / nice_total if nice_total else 1.0
    score = 100 * (FIT_CORE_WEIGHT * core + (1 - FIT_CORE_WEIGHT) * nice)
    score -= FIT_MISMATCH_PENALTY * mismatches + FIT_PARTIAL_PENALTY * partial
    return max(0, min(100, round(score)))


def skills_line(job) -> str:
    """«ключевые: ✔ Python, PostgreSQL ✘ Kafka · nice-to-have: ✔ Docker ✘ Go»."""
    parts = []
    for label, ok, bad in (("ключевые", getattr(job, "cv_core_have", []), getattr(job, "cv_core_missing", [])),
                           ("nice-to-have", getattr(job, "cv_nice_have", []), getattr(job, "cv_nice_missing", []))):
        if ok or bad:
            parts.append(f"{label}: " + " ".join(filter(None, [
                "✔ " + ", ".join(ok) if ok else "", "✘ " + ", ".join(bad) if bad else ""])))
    return " · ".join(parts)


def _norm_term(term: str) -> str:
    return re.sub(r"(?:-based|\.)$", "", term)


def summary(job) -> str:
    """Одна строка для отчёта."""
    if not getattr(job, "cv_have", None) and not getattr(job, "cv_missing", None):
        return ""
    fit = getattr(job, "cv_fit", -1)
    parts = [f"Fit {fit}%" if fit >= 0 else "", "✔ полное соответствие резюме" if job.cv_full else "",
             skills_line(job)]
    if job.cv_mismatch:
        parts.append("несоответствие: " + "; ".join(job.cv_mismatch))
    if job.cv_partial:
        parts.append("частично: " + ", ".join(job.cv_partial))
    if job.cv_pick:
        parts.append(f"резюме: {job.cv_pick}")
    return " · ".join(p for p in parts if p)
