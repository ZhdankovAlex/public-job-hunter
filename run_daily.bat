@echo off
rem Запуск для Планировщика заданий Windows
cd /d "%~dp0"
rem сначала новые письма (статусы откликов для отчёта), потом поиск вакансий
".venv\Scripts\python.exe" applications.py >> last_run.log 2>&1
".venv\Scripts\python.exe" main.py >> last_run.log 2>&1
