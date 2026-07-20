FROM public.ecr.aws/docker/library/python:3.12-slim AS base
# Run as non-root user (SEC-055, ADV-015)
RUN groupadd -r appuser && useradd -r -g appuser -d /app appuser
WORKDIR /app
COPY pyproject.toml setup.py ./
COPY src/ ./src/
RUN pip install --no-cache-dir .
COPY client.json ./
RUN chown -R appuser:appuser /app
USER appuser
CMD ["python", "-m", "ckn_ingestion", "--config", "./client.json"]
