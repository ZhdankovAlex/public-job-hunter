"""Ежедневный сбор вакансий -> reports/jobs_YYYY-MM-DD.md. Ничего никуда не отправляет."""
import argparse
import json
import logging
import re
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta, timezone

from jobhunter import (aggregators, applied, config, filters, freehire, geo, html_report, keys, report, sites,
                       telegram, verify)
from jobhunter.ats import PLATFORMS, AtsClient
from jobhunter.companies import load_companies
from jobhunter.http import Http, cancel as http_cancel
from jobhunter.models import norm_name
from jobhunter.relevance import Profile
from jobhunter import salary as salary_rules
from jobhunter.salary import SalaryBase
from jobhunter import cvgen
from jobhunter.cv_match import CvProfile
from jobhunter.applications_report import StatusIndex
from jobhunter import tiers
from jobhunter.staffing import Blacklist
from jobhunter import levels, progress

STAGES = [
    ("companies", "Список компаний"),
    ("freehire", "freehire.me: поиск"),
    ("sources", "ATS-борды, агрегаторы, карьерные страницы"),
    ("filter", "Фильтры и свежесть"),
    ("freehire_texts", "freehire.me: полные тексты и проверка"),
    ("sheets", "Отклики: applications_manual.toml и статусы из почты"),
    ("telegram", "Telegram-каналы"),
    ("telegram_check", "Проверка Telegram-вакансий"),
    ("scoring", "Сопоставление с резюме, баллы, зарплаты"),
    ("cv", "Резюме под вакансии"),
    ("reports", "Отчёты"),
]
SKIPPABLE = {"freehire", "sources", "telegram", "cv"}  # этапы с кнопкой «Пропустить» в веб-интерфейсе
SOURCES_FILE = config.ROOT / "state" / "sources_latest.json"  # сколько вакансий дал каждый источник
FREEHIRE_FILE = config.ROOT / "state" / "freehire_latest.json"  # запросы freehire: название, параметры, итог

log = logging.getLogger("jobhunter")


def setup_logging():
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
    console = progress.ConsoleHandler(sys.stdout)  # печатает лог над строкой прогресса
    console.setFormatter(fmt)
    progress.rotate_errors_log()
    errors = logging.FileHandler(config.ERRORS_LOG, encoding="utf-8")
    errors.setLevel(logging.WARNING)
    errors.setFormatter(fmt)
    logging.basicConfig(level=logging.INFO, handlers=[console, errors])
    logging.getLogger("urllib3").setLevel(logging.ERROR)


def parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--only", help="только эти компании (через запятую)")
    p.add_argument("--limit", type=int, help="только первые N компаний из списка")
    p.add_argument("--rediscover", action="store_true", help="заново искать ATS slug для всех компаний")
    p.add_argument("--no-aggregators", action="store_true", help="пропустить Arbeitnow/RemoteOK/Himalayas")
    p.add_argument("--no-freehire", action="store_true", help="пропустить freehire.me")
    p.add_argument("--no-telegram", action="store_true", help="пропустить Telegram-каналы")
    p.add_argument("--no-applied", action="store_true", help="не сверяться с откликами (applications_manual.toml)")
    return p.parse_args()


def collect(args, companies, http, ats, career_pages=()):
    """Все источники параллельно; лимит 1 req/s держится по каждому хосту отдельно."""
    jobs = []
    track = progress.tracked  # отмечает источник «в работе» на время запроса
    kinds: dict[str, str] = {}  # источник -> вид: ats / aggregator / site / career_page
    results: dict[str, dict] = {}  # итог по источнику -> state/sources_latest.json
    with ThreadPoolExecutor(max_workers=config.WORKERS) as pool:
        futures = {}
        for c in companies:
            futures[pool.submit(track, c.name, ats.jobs_for, c, args.rediscover)] = c.name
            kinds[c.name] = "ats"
        if not args.no_aggregators:
            for name in aggregators.SOURCES:
                futures[pool.submit(track, name, aggregators.fetch, name, http)] = name
                kinds[name] = "aggregator"
            for name in sites.enabled_sources():
                futures[pool.submit(track, name, sites.fetch, name, http)] = name
                kinds[name] = "site"
        for company, url, *location in career_pages:  # карьерные страницы без ATS
            futures[pool.submit(track, company.name, sites.career_page, http, company, url, *location)] = company.name
            kinds[company.name] = "career_page"
        progress.set_total(len(futures))
        try:
            for done, future in enumerate(as_completed(futures), 1):
                if progress.skip_requested():  # кнопка «Пропустить»: недоделанные источники отменяются
                    log.warning("Источники: пропуск по кнопке, обработано %d из %d", done - 1, len(futures))
                    http_cancel.set()  # идущие запросы обрываются, пул не ждёт долгие карьерные страницы
                    break
                name = futures[future]
                try:
                    found = future.result()
                    jobs.extend(found)
                    results[name] = {"kind": kinds[name], "jobs": len(found), "error": ats.failures.get(name, "")}
                    progress.tally("source_kinds", kinds[name], len(found))
                    if found:
                        progress.event("source", f"{name}: {len(found)} вакансий")
                except Exception as e:
                    log.exception("%s: непредвиденная ошибка — пропущено", name)
                    results[name] = {"kind": kinds[name], "jobs": 0, "error": f"{type(e).__name__}: {e}"[:200]}
                if done % 50 == 0:
                    log.info("Обработано источников: %d/%d", done, len(futures))
        finally:
            pool.shutdown(cancel_futures=True)
            http_cancel.clear()
            ats.save()
            for name, entry in results.items():
                entry.update((ats.mapping.get(name) or {}) if entry["kind"] == "ats" else {})
            SOURCES_FILE.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    return jobs


_REASON_GROUPS = [  # причина отказа -> короткая группа для диаграммы на странице пайплайна
    (("локация", "on-site", "привязка к стране", "только из России"), "локация"),
    (("язык", "JVM"), "другой язык"),
    (("full-time", "AI trainer"), "не full-time"),
    (("опыт от",), "опыт"),
    (("старше",), "старая"),
]


def _reason_group(reason: str) -> str:
    """«основной язык — Go» -> «другой язык»; неизвестная причина — как есть, до двоеточия."""
    for needles, group in _REASON_GROUPS:
        if any(n in reason for n in needles):
            return group
    return reason.split(":")[0][:40]


def save_freehire_queries(fh, settings: dict, jobs: list) -> None:
    """Запросы freehire последнего прогона -> state/freehire_latest.json (страница «Источники»).

    По каждому запросу: название из settings.toml, параметры, сколько вакансий на сайте всего
    и сколько получено (вакансия, найденная несколькими запросами, считается в каждом).
    """
    got = Counter(q for j in jobs for q in j.extra.get("queries", []))
    rows = [{
        "name": q["name"], "title": q.get("title", q["name"]),
        "params": fh.query_params.get(q["name"], {}),
        "total": fh.totals.get(q["name"]), "fetched": got.get(q["name"], 0),
        "skipped": q["name"] not in fh.query_params,  # например, релокация выключена
    } for q in settings.get("queries", [])]
    FREEHIRE_FILE.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")


def load_seen() -> dict[str, str]:
    """Ключ вакансии -> дата (ISO), когда скрипт увидел её впервые."""
    if config.SEEN_FILE.exists():
        return json.loads(config.SEEN_FILE.read_text(encoding="utf-8"))
    return {}


def is_fresh(job, seen: dict[str, str], today: date, max_age_days: int) -> bool:
    """Опубликована не раньше max_age_days назад; без даты — считаем от первого появления."""
    cutoff = today - timedelta(days=max_age_days)
    if job.posted:
        return job.posted.astimezone(timezone.utc).date() >= cutoff
    first_seen = seen.get(keys.norm_url(job.url))
    return first_seen is None or date.fromisoformat(first_seen) >= cutoff


def mark_first_seen(jobs, tg_jobs, today, seen):
    """Виденные вакансии хранятся между запусками; новая = впервые увидена сегодня."""
    keyed = [(job, keys.norm_url(job.url)) for job in jobs]
    keyed += [
        (job, f"tg:{keys.company_key(job.company)}|{norm_name(job.title)}") for job in tg_jobs
    ]
    for job, key in keyed:
        job.is_new_today = seen.setdefault(key, today.isoformat()) == today.isoformat()
    config.SEEN_FILE.parent.mkdir(exist_ok=True)
    config.SEEN_FILE.write_text(json.dumps(seen, indent=0, sort_keys=True), encoding="utf-8")


def _section_rank(job):
    if job.reloc_type:
        return 1 + geo.TYPE_ORDER.index(job.reloc_type)
    return 99 if job.location_check else 0


def must_report(ats, fetched, matched):
    """Строки сводки по обязательным компаниям: найдено / прошло фильтры / причина."""
    found = Counter(j.company for j in fetched if j.source in PLATFORMS)
    passed = Counter(j.company for j in matched if j.source in PLATFORMS)
    rows = []
    for name in geo.S.get("must_report_companies", []):
        entry = ats.mapping.get(name)
        override = geo.S.get("ats_overrides", {}).get(name, {}).get("greenhouse_board_token")
        if name in ats.failures:
            reason = ats.failures[name]
        elif override or (entry and entry.get("platform")):
            reason = ""
        elif entry is None:
            reason = "проверка не завершена: сбой сети или 429 при подборе slug"
        else:
            reason = "slug не подошёл ни на Greenhouse, ни на Lever, ни на Ashby, ни на Workable"
            if entry.get("empty_board"):
                reason += f"; есть только пустой борд {entry['empty_board']}"
        if reason:  # кэшированное "не найдено" иначе не попадало бы в errors.log каждый день
            log.warning("%s: ATS-борд недоступен — %s", name, reason)
        rows.append((name, found[name], passed[name], reason))
    return rows


def enrich_from_ats(tg_jobs, ats):
    """Для постов со ссылкой на Greenhouse/Lever/Ashby/… подтягивает полный текст вакансии с борда."""
    from jobhunter.models import Company

    boards: dict[tuple[str, str], list] = {}
    for job in tg_jobs:
        if job.full_text or not job.url:
            continue
        board = keys.ats_from_link(job.url)
        if board:
            boards.setdefault(board, []).append(job)
    filled = 0
    for (platform, slug), posts in boards.items():
        try:
            board_jobs = ats._fetch(platform, slug, Company(posts[0].company)) or []
        except Exception as e:  # сеть, формат — пост останется со статусом ❓
            log.warning("telegram: не удалось прочитать борд %s/%s (%s)", platform, slug, e)
            continue
        by_url = {keys.norm_url(j.url): j for j in board_jobs}
        by_id = {i: j for j in board_jobs for i in keys.url_ids(j.url)}
        for post in posts:
            twin = by_url.get(keys.norm_url(post.url)) or next(
                (by_id[i] for i in keys.url_ids(post.url) if i in by_id), None)
            if twin and twin.description:
                post.text = twin.description
                post.full_text = True
                if twin.location and not post.location:
                    post.location = twin.location
                filled += 1
    return filled


# Сколько НОВЫХ компаний из Telegram-постов без полного текста искать на ATS-бордах за прогон: поиск борда — до
# десятка запросов на компанию (Workable — раз в 6 с); результат, и «не найдено» тоже, кэшируется в
# ats_mapping.json, поэтому известные компании в лимит не входят и следующие прогоны идут быстро
TG_BOARD_LOOKUPS = 25


def _title_key(title: str) -> str:
    return re.sub(r"[^a-z0-9а-яё]+", " ", title.lower()).strip()


_TG_BOARD_CACHE: dict[str, list] = {}  # компания -> вакансии её борда (в пределах прогона)


def _enrich_from_company_boards(tg_jobs, ats, lookups: dict | None = None) -> int:
    """Пост без полного текста (дайджест со ссылкой на агрегатор, например wantapply.com, — у него защита от
    ботов) -> ищем борд компании (Greenhouse/Lever/Ashby/Workable) и берём текст вакансии с тем же названием.
    Так видны требования, которых нет в посте: офис, язык, опыт."""
    from jobhunter.models import Company

    filled, looked = 0, _TG_BOARD_CACHE
    lookups = lookups if lookups is not None else {"new": 0}
    for job in tg_jobs:
        if job.full_text or not job.company or not filters.title_candidate(job.title):
            continue
        name = job.company.strip()
        if name not in looked:
            if name not in ats.mapping:
                if lookups["new"] >= TG_BOARD_LOOKUPS:
                    continue
                lookups["new"] += 1
            try:
                # quiet: «борд не найден» для компании из поста — норма, а не повод что-то делать
                looked[name] = ats.jobs_for(Company(name), quiet=True) or []
            except Exception as e:  # сеть, формат — пост останется с текстом из Telegram
                log.warning("telegram: борд %s не прочитан (%s)", name, e)
                looked[name] = []
        key = _title_key(job.title)
        twin = next((b for b in looked[name] if b.description and _title_key(b.title) == key), None)
        if twin:
            job.text = twin.description
            job.full_text = True
            if twin.location and not job.location:
                job.location = twin.location
            filled += 1
    return filled


def merge_duplicates(jobs):
    """Одна вакансия (компания + название) в нескольких локациях -> одна строка."""
    merged = {}
    for job in jobs:
        key = (keys.company_key(job.company), norm_name(job.title))
        base = merged.get(key)
        if base is None:
            merged[key] = job
            continue
        locations = list(dict.fromkeys(
            part.strip() for j in (base, job) for part in j.location.split(";") if part.strip()
        ))
        # раздел берём от лучшей из локаций: ✅ > 🌍 (подтверждённая раньше) > ❓
        best = min(base, job, key=_section_rank)
        sources = list(dict.fromkeys([*(base.sources or [base.source]), *(job.sources or [job.source])]))
        extra = base.extra or job.extra
        geo_fields = (best.location_check, best.reloc_country, best.reloc_type, best.reloc_quote)
        if (job.posted and job.posted.timestamp() or 0) > (base.posted and base.posted.timestamp() or 0):
            merged[key] = base = job
        base.location = "; ".join(locations)
        base.sources, base.extra = sources, extra
        base.location_check, base.reloc_country, base.reloc_type, base.reloc_quote = geo_fields
    return list(merged.values())


def main():
    args = parse_args()
    setup_logging()
    pipeline = progress.Pipeline("main", STAGES, skippable=SKIPPABLE)
    try:
        run(args)
    except BaseException:  # в том числе Ctrl+C и sys.exit: этап и прогон отмечаются как упавшие
        log.exception("Прогон прерван")
        pipeline.finish(ok=False)
        raise
    pipeline.finish()


def run(args):
    """Весь прогон: сбор, фильтры, Telegram, сопоставление с резюме, отчёты."""
    today = date.today()

    progress.start("companies")
    companies = load_companies()
    scores = {norm_name(c.name): c for c in companies}
    if args.only:
        wanted = {norm_name(n) for n in args.only.split(",")}
        companies = [c for c in companies if norm_name(c.name) in wanted]
    if args.limit:
        companies = companies[: args.limit]

    http = Http()
    fh_settings = geo.S.get("freehire", {})
    fh = freehire.Freehire(http, fh_settings)
    fh_jobs = []
    if fh_settings.get("enabled", True) and not args.no_freehire:
        progress.start("freehire")
        try:  # основной источник идёт первым; отброшенный API параметр останавливает прогон
            fh_jobs = fh.collect()
        except freehire.IgnoredParamsError as e:
            log.error("%s", e)
            sys.exit(2)
        save_freehire_queries(fh, fh_settings, fh_jobs)
    else:
        progress.skip("freehire", "отключено")
    ats = AtsClient(http)
    log.info("Компаний к проверке: %d", len(companies))
    by_name = {c.name: c for c in companies}
    career_pages = []
    # страницы из settings.toml [career_pages]: компания = ссылка или { url, location }
    for name, page in geo.S.get("career_pages", {}).items():
        if name in by_name:
            page = page if isinstance(page, dict) else {"url": page}
            career_pages.append((by_name[name], page["url"], page.get("location", "")))
    # аутсорс (staffing_blacklist.txt) по карьерным страницам не обходим: его вакансии всё равно уходят в
    # отдельный раздел, а страницы у таких компаний огромные или битые
    staffing = Blacklist()
    career_pages = [p for p in career_pages if not staffing.reason(p[0].name, "", "")]
    progress.start("sources")
    fetched = collect(args, companies, http, ats, career_pages)
    progress.count(fetched=len(fetched))
    progress.start("filter", total=len(fetched))

    seen = load_seen()
    max_age_days = int(geo.S.get("max_age_days", 30))
    matched, rejected, seen_urls, stale = [], [], set(), 0
    for job in fetched:
        known = scores.get(norm_name(job.company))
        if known and not job.score:  # вакансия из агрегатора, компания из нашего списка
            job.score, job.note = known.score, known.note
        key = keys.norm_url(job.url) if job.url else ""
        progress.step(f"{job.company} — {job.title}")
        if key in seen_urls:
            progress.tally("rejects", "дубль")
            continue
        if not filters.apply(job):
            if job.reject:  # роль подходит, но сработало другое правило — покажем в «Отброшено»
                seen_urls.add(key)
                rejected.append(job)
                progress.tally("rejects", _reason_group(job.reject))
                progress.event("reject", f"{job.company} — {job.title}: {job.reject}")
            else:
                progress.tally("rejects", "не та роль")
            continue
        seen_urls.add(key)
        priority = keys.company_key(job.company) in geo.PRIORITY_COMPANIES
        if not priority and not is_fresh(job, seen, today, max_age_days):
            stale += 1
            job.reject = f"старше {max_age_days} дней"
            rejected.append(job)
            progress.tally("rejects", "старая")
            continue
        matched.append(job)
        progress.tally("rejects", "прошла")
        progress.event("found", f"{job.company} — {job.title} ({job.location or 'remote'})")

    # freehire: совпавшие с уже найденными (по URL или компании+названию) только помечаем;
    # для остальных запрашиваем полный текст и проверяем по нему
    progress.count(matched=len(matched))
    progress.start("freehire_texts")
    by_url = {keys.norm_url(j.url): j for j in matched}
    by_title = {(keys.company_key(j.company), norm_name(j.title)): j for j in matched}
    max_experience = int(fh_settings.get("max_experience_years", 7))
    fh_added = fh_twins = 0
    fh_candidates = []
    for job in fh_jobs:
        url_key, title_key = keys.norm_url(job.url), (keys.company_key(job.company), norm_name(job.title))
        twin = by_url.get(url_key) or by_title.get(title_key)
        if twin is not None:
            if not twin.extra:
                fh_twins += 1
                twin.extra = job.extra
                twin.sources = ["freehire", *(twin.sources or [twin.source])]
            continue
        known = scores.get(norm_name(job.company))
        if known:
            job.score, job.note = known.score, known.note
        if freehire.prefilter(job, max_experience):
            fh_candidates.append(job)
        elif job.reject:
            rejected.append(job)
            progress.tally("rejects", _reason_group(job.reject))
    fh_candidates = fh_candidates[: int(fh_settings.get("descriptions_top", 200))]
    missing_text = [j for j in fh_candidates if not j.extra.get("full_description")]
    if missing_text:  # поиск уже отдаёт полный текст; дозапрос — только для редких исключений
        fh.fill_descriptions(missing_text)
    fh_described = sum(bool(j.extra.get("full_description")) for j in fh_candidates)
    for job in fh_candidates:
        if freehire.prepare(job):
            fh_added += 1
            matched.append(job)
            progress.tally("rejects", "прошла")
            progress.event("found", f"{job.company} — {job.title} (freehire)")
        else:
            rejected.append(job)
            progress.tally("rejects", _reason_group(job.reject or "не та роль"))
    fh_dropped = len(fh_candidates) - fh_added
    fh.diagnose_empty()

    blocked_urls = {keys.norm_url(u) for u in geo.S.get("blocked_urls", [])}
    matched = [j for j in matched if keys.norm_url(j.url) not in blocked_urls]

    progress.start("sheets")
    apps = None if args.no_applied else applied.load_applications()
    try:  # статусы откликов из почты (локальный кэш) и applications_manual.toml
        app_index = StatusIndex(apps)
    except Exception:
        log.exception("Статусы откликов прочитать не удалось — пометок «📬» не будет")
        app_index = None
    fresh = []
    for job in matched:
        if apps and apps.has_job(job):
            continue
        if apps:
            job.applied_status = apps.company_statuses(job.company)
            status = apps.job_status(job)
            if status is not None:
                job.history_marks.append(f"🔁 на эту вакансию уже был отклик: {status or 'без статуса'}")
        fresh.append(job)
    unmerged = len(fresh)
    fresh = merge_duplicates(fresh)
    blacklist = Blacklist()
    for job in fresh:
        job.staffing = blacklist.reason(job.company, job.title, job.description)
        job.app_status = app_index.lookup(job.company) if app_index else ""
        job.referral = keys.company_key(job.company) in geo.REFERRAL_COMPANIES

    use_telegram = config.TELEGRAM_ENABLED and config.TELEGRAM_CHANNELS and not args.no_telegram
    if not use_telegram:
        why = "--no-telegram" if args.no_telegram else "выключено в settings.toml [telegram]"
        progress.skip("telegram", why)
        progress.skip("telegram_check", why)
    else:
        progress.start("telegram", total=len(config.TELEGRAM_CHANNELS))
    tg_all, tg_failed = telegram.collect(http) if use_telegram else (None, [])
    tg_jobs = None if tg_all is None else []
    if tg_all is not None:
        progress.start("telegram_check", total=len(tg_all))
    tg_enriched = enrich_from_ats(tg_all or [], ats)
    lookups = {"new": 0}  # сколько новых компаний уже искали на ATS в этом прогоне (лимит TG_BOARD_LOOKUPS)
    for job in tg_all or []:  # Telegram: те же проверки по тексту; с таблицами — только пометки
        progress.step(f"@{job.channel}: {job.title}")
        verify.check(job, job.text, job.full_text)
        # пост прошёл по короткому тексту (дайджест со ссылкой на wantapply и т.п.) — берём полный текст с
        # борда компании и проверяем ещё раз: требования вроде офиса или языка есть только там
        if not job.reject and not job.full_text and _enrich_from_company_boards([job], ats, lookups):
            tg_enriched += 1
            verify.check(job, job.text, job.full_text)
        if job.reject:
            rejected.append(job)
            progress.tally("rejects", _reason_group(job.reject))
            progress.event("reject", f"@{job.channel}: {job.company} — {job.title}: {job.reject}")
            continue
        progress.tally("rejects", "прошла")
        progress.event("found", f"@{job.channel}: {job.company} — {job.title}")
        if apps:
            job.applied_status = apps.company_statuses(job.company)
        job.staffing = blacklist.reason(job.company, job.title, job.text)
        job.app_status = app_index.lookup(job.company) if app_index else ""
        if job.app_status:
            job.marks.append(f"📬 {job.app_status}")
        tg_jobs.append(job)
    mark_first_seen(fresh, tg_jobs or [], today, seen)

    progress.count(new=len(fresh), telegram=len(tg_jobs or []))
    progress.start("scoring")
    cv = CvProfile(geo.S.get("cv", {}))
    for job in fresh:
        cv.analyze(job, job.description)
    for job in tg_jobs or []:
        cv.analyze(job, job.text)
    for job in [*fresh, *(tg_jobs or [])]:
        job.tier = tiers.tier(job)

    profile = Profile()
    for job in fresh:
        job.relevance, job.matched_skills = profile.score(
            job, job.description, job.lang_marks, profile.location_points(job)
        )
    for job in tg_jobs or []:
        job.relevance, job.matched_skills = profile.score(
            job, job.text, job.marks, profile.tg_location_points(job)
        )
    # зарплаты: пополняем базу вилок и подписываем каждую вакансию (указанная вилка или оценка)
    salaries = SalaryBase()
    for job in telegram.SALARY_POSTS:
        market = "cis" if job.channel in config.TELEGRAM_CIS_CHANNELS else "global"
        salaries.add(f"tg:{job.post_url}:{norm_name(job.title)}", job.title, job.salary, market,
                     f"@{job.channel}", (job.posted or today).isoformat()[:10])
    for job in [*fetched, *fh_jobs]:
        if job.salary:
            salaries.add(keys.norm_url(job.url), job.title, job.salary, "global", job.source,
                         (job.posted.date() if job.posted else today).isoformat())
    salaries.save()
    for job in [*fresh, *(tg_jobs or [])]:
        job.grade = salary_rules.grade(job.title, getattr(job, "description", "") or getattr(job, "text", ""))
        job.salary_note = salaries.describe(job.title, job.salary, job.grade)
    # медиана по стране вакансии с levels.fyi (кэш на неделю — за прогон единицы запросов)
    market = levels.Levels(http)
    for job in [*fresh, *(tg_jobs or [])]:
        job.levels_note, job.levels_url = market.describe(job.location, getattr(job, "reloc_country", ""))
    market.save()

    # «исходный балл» отброшенных — только навыки и название, без поправок за локацию
    unique_rejected = {}
    for job in rejected:
        text = getattr(job, "description", None) or getattr(job, "text", "")
        job.relevance, job.matched_skills = profile.base_score(job, text)
        key = (keys.company_key(job.company), norm_name(job.title))
        if key not in unique_rejected or job.relevance > unique_rejected[key].relevance:
            unique_rejected[key] = job
    rejected = sorted(unique_rejected.values(), key=lambda j: -j.relevance)
    for job in rejected:
        if apps:
            job.applied_status = apps.company_statuses(job.company)
    rejected_file = config.ROOT / "state" / "rejected_latest.tsv"
    rejected_file.write_text(
        "score\tcompany\ttitle\treason\tquote\turl\n" + "\n".join(
            "\t".join(str(x).replace("\t", " ").replace("\n", " ") for x in (
                j.relevance, j.company, j.title, j.reject, j.reject_quote, j.url or getattr(j, "post_url", "")))
            for j in rejected),
        encoding="utf-8",
    )

    stats = {
        "fetched": len(fetched),
        "matched": len(matched),
        "in_applied": len(matched) - unmerged,
        "companies": len(companies),
        "mapped": sum(1 for c in companies if (ats.mapping.get(c.name) or {}).get("platform")),
        "tg_failed": tg_failed,
        "merged": unmerged - len(fresh),
        "stale": stale,
        "salary": {"samples": len(salaries.samples), "summary": salaries.summary()},
        "tg_enriched": tg_enriched,
        "freehire": {
            "enabled": bool(fh_jobs) or fh.requests > 0 or fh.cache_hits > 0,
            "fetched": len(fh_jobs), "added": fh_added, "twins": fh_twins,
            "requests": fh.requests, "cache_hits": fh.cache_hits, "totals": fh.totals,
            "described": fh_described, "dropped": fh_dropped,
            "diagnosis": fh.diagnosis, "candidates": len(fh_candidates),
        },
        "rejected": rejected,
        "applications": app_index.digest() if app_index else None,
        "max_age_days": max_age_days,
        "referral": [(n, bool((ats.mapping.get(n) or {}).get("platform")))
                     for n in geo.S.get("referral_companies", [])],
        "must_report": must_report(ats, fetched, matched),
    }
    config.REPORTS_DIR.mkdir(exist_ok=True)
    # резюме под новые вакансии с высоким Fit — ссылка «📄 резюме» в HTML-отчёте
    if cvgen.AUTO_MAX > 0:
        progress.start("cv")
        cvgen.auto_build([*fresh, *(tg_jobs or [])], today)
    else:
        progress.skip("cv", "автосборка выключена: резюме — по кнопке в отчёте")
    progress.start("reports")
    out = config.REPORTS_DIR / f"jobs_{today.isoformat()}.md"
    out.write_text(report.render(today, fresh, stats, tg_jobs), encoding="utf-8")
    page = html_report.render(today, fresh, stats, tg_jobs)
    html_out = config.REPORTS_DIR / f"jobs_{today.isoformat()}.html"
    html_out.write_text(page, encoding="utf-8")
    (config.REPORTS_DIR / "latest.html").write_text(page, encoding="utf-8")  # всегда свежий отчёт по одному адресу
    # вакансии отчёта с текстом — для резюме под вакансию (cvgen.py, кнопка «📄 CV» в отчёте);
    # копия по дате — чтобы резюме собиралось и из старых отчётов
    jobs_json = json.dumps([
        {"url": j.url or getattr(j, "post_url", ""), "company": j.company, "title": j.title,
         "location": j.location, "text": getattr(j, "description", "") or getattr(j, "text", ""), "fit": j.cv_fit}
        for j in [*fresh, *(tg_jobs or [])]
    ], ensure_ascii=False)
    (config.ROOT / "state" / "jobs_latest.json").write_text(jobs_json, encoding="utf-8")
    config.JOBS_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    (config.JOBS_ARCHIVE_DIR / f"{today.isoformat()}.json").write_text(jobs_json, encoding="utf-8")
    progress.count(rejected=len(rejected), new_today=sum(j.is_new_today for j in [*fresh, *(tg_jobs or [])]))
    log.info("Готово: %d новых из %d подходящих -> %s и %s", len(fresh), len(matched), out, html_out)


if __name__ == "__main__":
    main()
