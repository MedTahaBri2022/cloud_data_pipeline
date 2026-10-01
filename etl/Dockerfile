# Self-contained image of the pipeline: what the Kubernetes CronJob runs.
# Build from the repository root:  docker build -f etl/Dockerfile -t retail-etl .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SQL_DIR=/app/sql \
    DATA_DIR=/app/data/raw

WORKDIR /app
COPY etl/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY etl/pipeline ./pipeline
COPY sql ./sql
COPY data/raw ./data/raw

RUN useradd --create-home --uid 10001 pipeline
USER pipeline

ENTRYPOINT ["python", "-m", "pipeline"]
CMD ["all"]
