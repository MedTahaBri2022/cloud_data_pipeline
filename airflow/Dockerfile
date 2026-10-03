FROM apache/airflow:2.10.5-python3.12

# The official image already ships psycopg2; the pipeline only adds the
# MongoDB driver. Kept in sync with etl/requirements.txt, except the Google
# libraries: the export to BigQuery runs as a Cloud Run job, not in Airflow.
RUN pip install --no-cache-dir "pymongo>=4.10,<5"
