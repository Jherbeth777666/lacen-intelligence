FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY etl ./etl
COPY db ./db
COPY scripts ./scripts
COPY wsgi.py .

RUN useradd --create-home lacen && chown -R lacen /app
USER lacen

EXPOSE 8000
ENV PORT=8000 WEB_CONCURRENCY=2 PRODUCAO=1
HEALTHCHECK CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://localhost:{os.environ[\"PORT\"]}/saude')" || exit 1
# PORT é definido pela hospedagem (Render, Fly, Cloud Run); 2 processos x 4 threads cabem em 512 MB
CMD gunicorn -w ${WEB_CONCURRENCY} --threads 4 -b 0.0.0.0:${PORT} --timeout 300 --access-logfile - wsgi:app
