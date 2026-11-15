FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY pyproject.toml ./
COPY app ./app
RUN pip install ".[postgres]"

COPY alembic.ini ./
COPY alembic ./alembic

RUN useradd -m finn && mkdir -p /app/var && chown -R finn /app
USER finn

EXPOSE 8000
# Migrations run on start (idempotent); then the API. Render injects $PORT.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
