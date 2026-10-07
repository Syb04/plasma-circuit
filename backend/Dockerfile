# syntax=docker/dockerfile:1
FROM python:3.12.10-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN --mount=type=secret,id=proxy_ca \
    apt-get update && apt-get install -y --no-install-recommends libngspice0 ngspice ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt requirements-dev.txt requirements.lock ./
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then export PIP_CERT=/run/secrets/proxy_ca; fi; \
    pip install --no-cache-dir -r requirements.lock
COPY app ./app
COPY tests ./tests
EXPOSE 8000
CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000"]
