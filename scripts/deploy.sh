#!/usr/bin/env bash
# Deploy the CDK stack to LocalStack with cdklocal (bootstrap first, both idempotent).
set -euo pipefail
cd "$(dirname "$0")/.."
export AWS_ENDPOINT_URL=${AWS_ENDPOINT_URL:-http://localhost:45671}
# cdklocal publishes assets to S3 by virtual-host name; *.localhost.localstack.cloud resolves to 127.0.0.1.
export AWS_ENDPOINT_URL_S3=${AWS_ENDPOINT_URL_S3:-http://s3.localhost.localstack.cloud:${AWS_ENDPOINT_URL##*:}}
export AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test AWS_DEFAULT_REGION=us-east-1 AWS_REGION=us-east-1
mkdir -p .runtime
[ -d infra/node_modules ] || (cd infra && npm ci --no-audit --no-fund)
cd infra
npx cdklocal bootstrap aws://000000000000/us-east-1 > ../.runtime/cdk-bootstrap.log 2>&1
npx cdklocal deploy --require-approval never --outputs-file ../.runtime/cdk-outputs.json > ../.runtime/cdk-deploy.log 2>&1 \
  || { tail -30 ../.runtime/cdk-deploy.log; exit 1; }
echo "deployed: $(grep -o '"TableName": "[^"]*"' ../.runtime/cdk-outputs.json)"
