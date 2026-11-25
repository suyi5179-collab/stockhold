# LocalStack memory (container limit 1536m), measured with `docker exec stockhold-localstack-1 ps -eo rss,args`

| when | kinesis-local (node, serves DynamoDB Streams) | localstack python | dynamodb-local java | container (docker stats) |
|---|---|---|---|---|
| after the step-07 test runs + simulations, no cap | 601152 KB | 449136 KB | 257444 KB | 1.259 GiB / 1.5 GiB |
| fresh stack + full test suite (67 tests), `NODE_OPTIONS=--max-old-space-size=160` | 173260 KB | 381152 KB | 202884 KB | 754.2 MiB / 1.5 GiB |

Before the 1536m limit, at 1100m, the LocalStack main process was OOM-killed during a 150-buyer burst (`docker inspect` showed `OOMKilled=true` while the container stayed "Up": only the supervisor and the Java process were left).
