FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

RUN apt-get update \
    && apt-get install -y --no-install-recommends cron tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

ENV UV_SYSTEM_PYTHON=1 \
    UV_COMPILE_BYTECODE=1

COPY requirements.txt .
RUN uv pip install -r requirements.txt

COPY test_call.py .
COPY crontab /etc/cron.d/twilio-test
COPY entrypoint.sh /entrypoint.sh

RUN chmod 0644 /etc/cron.d/twilio-test \
    && chmod +x /entrypoint.sh \
    && mkdir -p /app/results

ENTRYPOINT ["/entrypoint.sh"]