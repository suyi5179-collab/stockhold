# 08 观测：一条 trace 穿过队列，指标和告警从真实存储里读

> 视频：[p08-observability.mp4](../videos/p08-observability.mp4) · tag：`step-08`

## 这一章解决什么问题？

前面七章，出了问题只能翻日志。半夜告警响了，on-call 想知道：慢在哪一步？死信多不多、最老的那条等了多久？有没有超卖？
AWS 上是 X-Ray 和 CloudWatch。本地的替代：

| AWS | 这里 | 在哪 |
|---|---|---|
| X-Ray | OpenTelemetry + Jaeger（`http://127.0.0.1:16691`） | `stockhold/tracing.py` |
| CloudWatch 指标 | Prometheus（`http://127.0.0.1:9191`）抓 Flask 的 `/metrics` | `stockhold/metrics.py` |
| CloudWatch 告警 | Prometheus 告警规则 | `deploy/prometheus/rules.yml`（锚点 `alert-rules`） |
| CloudWatch 仪表盘 | Grafana（`http://127.0.0.1:3091`），dashboard JSON 在仓库里，启动时自动加载 | `deploy/grafana/` |

## 一条 trace 怎么穿过 SQS？

队列把调用链切断了：API 发了消息就返回，Lambda 是另一个进程、另一个容器。
做法是 W3C `traceparent`：发消息时把当前 span 写进 SQS 的 message attribute，Lambda 收到以后把它当作父 span（`payments.publish_raw`、`expiry.schedule`、`lambdas/payments.py`）。

Lambda 里没有 OpenTelemetry SDK（包里只有标准库和 boto3），所以 `tracing.py` 有两条路：Flask 里用官方 SDK；Lambda 里用一个四十行的手写发送器，按 OTLP/HTTP 的 JSON 格式直接发给 Jaeger。业务代码只写 `with tracing.span(...)`，不用管自己在哪。

## 真实跑一次

教学模式提交一个付款失败的样例，Jaeger 里同一个 trace id 下的 span：

```
stockhold-api       POST /learn/api/start   79 ms
stockhold-api       learn.submit            46 ms
stockhold-api       inventory.reserve       29 ms
stockhold-api       payment.webhook         10 ms
stockhold-payments  payments.handle        126 ms
stockhold-payments  hold.transition         93 ms
```

`tests/test_observability.py::test_one_trace_from_api_through_the_queue_into_the_lambda`（锚点 `test-trace-e2e`）断言这一点：同一个 trace 里同时有 `stockhold-api` 和 `stockhold-payments` 两个服务。

## 指标从哪来？

两类：

- 进程内的计数器和直方图：reserve 的结果、每次 reserve 的冲突次数、延迟、重放结果。
- 抓取时现读的 gauge：三个队列的深度、死信归档里没处理的条数和最老的年龄、十五分钟内进死信的条数、过期了还是 HELD 的 hold 数、演示商品的超卖件数。缓存五秒，抓得再频繁也只读一次。

告警四条：死信最老超过五分钟、十五分钟内进死信五条以上、超卖大于零（永远不该响）、过期了没释放的 hold 持续两分钟。前两条和重放工具顶部显示的是同一套阈值。

## 真实踩到的坑：LocalStack 自己吃掉了 1.26 GB

跑完测试和模拟以后，`docker stats` 显示 LocalStack 用了 1.259 GiB（上限 1.5）。进容器看每个进程：

```
601152 node /var/lib/localstack/lib/kinesis-local/0.4.13/node_modules/kinesis-local/main.js
449136 python -m localstack.runtime.main
257444 java … dynamodb-local
```

占得最多的不是 DynamoDB，是 Kinesis 模拟器：LocalStack 用它来实现 DynamoDB Streams（第七章加的）。给它的 V8 堆加上限 `NODE_OPTIONS=--max-old-space-size=160` 以后，同样跑完全部测试，它是 173 MB，整个容器 754 MiB。记录在 `docs/results/localstack_memory.md`，过程见 [problems.md 的 q05](../problems.md)。

## 怎么 checkout 到这一步跑起来

```bash
git checkout step-08
./scripts/demo.sh
open http://127.0.0.1:16691  http://127.0.0.1:9191/alerts  http://127.0.0.1:3091
.venv/bin/pytest tests/test_observability.py
```
