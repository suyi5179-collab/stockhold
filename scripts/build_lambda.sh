#!/usr/bin/env bash
# Assemble build/lambda: the handlers plus the stockhold core modules they import (stdlib + boto3 only).
set -euo pipefail
cd "$(dirname "$0")/.."
rm -rf build/lambda && mkdir -p build/lambda/stockhold
cp lambdas/*.py build/lambda/
for m in __init__ config aws dynamo keys states inventory expiry payments faults eventlog tracing; do
  [ -f "stockhold/$m.py" ] && cp "stockhold/$m.py" build/lambda/stockhold/
done
# Fixed mtimes so the CDK asset hash only changes when the code does.
find build/lambda -exec touch -t 202001010000 {} +
echo "build/lambda: $(ls build/lambda build/lambda/stockhold | grep -c '\.py')"
