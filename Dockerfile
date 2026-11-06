# job-hunter в контейнере: веб-интерфейс + скрипты. Код и данные — с хоста (volume в docker-compose.yml),
# в образе только Python-зависимости и Chromium для PDF-резюме.
FROM python:3.11-slim

# Chromium — PDF-резюме (cvgen), шрифты: Liberation Sans по метрикам совпадает с Arial из шаблона резюме
RUN apt-get update \
    && apt-get install -y --no-install-recommends chromium fonts-liberation fonts-dejavu-core tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

ENV PYTHONIOENCODING=utf-8 \
    PYTHONUNBUFFERED=1 \
    JOBHUNTER_CHROME=/usr/bin/chromium \
    JOBHUNTER_HOST=0.0.0.0 \
    TZ=UTC

EXPOSE 8765
CMD ["python", "-m", "web", "--no-browser"]
