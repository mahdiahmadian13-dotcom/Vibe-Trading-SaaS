# Gateway Dockerfile
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl gcc && rm -rf /var/lib/apt/lists/*

COPY gateway/requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY shared/ /app/shared/
COPY gateway/app/ /app/app/
# node bundle sources (served to joining servers via /api/v1/node/{token}/bundle)
COPY worker/ /app/worker_bundle/worker/
COPY agent/ /app/worker_bundle/agent/
COPY shared/ /app/worker_bundle/shared/
COPY docker-compose.node.yml /app/worker_bundle/docker-compose.node.yml
COPY bot/app/pdf_report.py /app/app/pdf_report.py
COPY bot/assets/ /app/assets/

EXPOSE 9000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "9000", "--workers", "4"]
