#!/bin/bash
set -e

# Cron doesn't inherit Docker env vars — forward the ones we need
env | grep -E "^(TWILIO|FROM_NUMBER|TEST_NUMBER|PLANTONIST|CSV_FILE)=" \
    >> /etc/environment

echo "[cron] Starting with TZ=America/Fortaleza"
echo "[cron] Schedule: every 10 min | Mon–Fri | 08:00–18:00 BRT"

exec cron -f