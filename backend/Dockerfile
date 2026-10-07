FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY backend/pyproject.toml ./pyproject.toml
COPY backend/src ./src
COPY backend/web ./web
COPY backend/migrations ./migrations
COPY backend/seed ./seed

RUN pip install --no-cache-dir . \
    && useradd --system --uid 10001 --create-home queryimmune

USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "queryimmune_db.main:app", "--host", "0.0.0.0", "--port", "8000"]
