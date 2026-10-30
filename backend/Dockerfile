# Build from the repository root: docker build -f backend/Dockerfile .
FROM python:3.14-slim

WORKDIR /srv/backend
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY backend/alembic ./alembic
COPY backend/alembic.ini .
COPY backend/scripts ./scripts
# scripts.seed reads the pack from the repository root, which here is /srv.
COPY festos_dataset_pack /srv/festos_dataset_pack

RUN useradd --system --no-create-home festos
USER festos

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/ready')"
CMD ["sh", "-c", "python -m alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips=*"]
