#!/usr/bin/env bash
# One command: LocalStack (DynamoDB, SQS, Lambda, S3, EventBridge), the CDK stack, demo data, the Flask app.
# Ctrl-C stops the app; `docker-compose -p stockhold down` stops the containers.
set -euo pipefail
cd "$(dirname "$0")/.."
COMPOSE=${COMPOSE:-docker-compose}
PY=/Library/Frameworks/Python.framework/Versions/3.11/bin/python3.11
command -v "$PY" >/dev/null || PY=python3.11
mkdir -p .runtime
echo $$ > .runtime/demo.pid
[ -d .venv ] || { "$PY" -m venv .venv && .venv/bin/pip install -q -r requirements.txt; }
$COMPOSE -p stockhold up -d --wait
./scripts/deploy.sh
export STOCKHOLD_SEED_DEMO=1 STOCKHOLD_BACKGROUND=1
.venv/bin/python -m stockhold.seed
.venv/bin/flask --app stockhold.app:create_app run --port 8840 --host 127.0.0.1 > .runtime/app.log 2>&1 &
echo $! > .runtime/app.pid
trap 'kill "$(cat .runtime/app.pid)" 2>/dev/null; rm -f .runtime/app.pid' INT TERM EXIT
for _ in $(seq 60); do curl -fs localhost:8840/healthz >/dev/null && break; sleep 1; done
echo
echo "  app         http://127.0.0.1:8840   (/ops replay tool: oncall / oncall-demo, viewer / viewer-demo)"
echo "  logs        .runtime/app.log"
echo
wait
