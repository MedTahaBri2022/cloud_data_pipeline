# Self-contained image of the pipeline: what the Kubernetes CronJob and the
# Cloud Run export job run.
# Build from the repository root:  docker build -f etl/Dockerfile -t retail-etl .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SQL_DIR=/app/sql \
    DATA_DIR=/app/data/raw \
    BQ_SCHEMA_DIR=/app/gcp/bigquery/schemas

WORKDIR /app
COPY etl/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY etl/pipeline ./pipeline
COPY sql ./sql
COPY data/raw ./data/raw
COPY gcp/bigquery/schemas ./gcp/bigquery/schemas

RUN useradd --create-home --uid 10001 pipeline
USER pipeline

ENTRYPOINT ["python", "-m", "pipeline"]
CMD ["all"]
