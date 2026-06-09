FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends cron tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY test_call.py .
COPY crontab /etc/cron.d/twilio-test
COPY entrypoint.sh /entrypoint.sh

RUN chmod 0644 /etc/cron.d/twilio-test \
    && chmod +x /entrypoint.sh \
    && mkdir -p /app/results

ENTRYPOINT ["/entrypoint.sh"]