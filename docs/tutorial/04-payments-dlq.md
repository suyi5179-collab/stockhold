# 04 付款事件：失败重试，坏消息进死信队列

> 视频：[p04-payments-dlq.mp4](../videos/p04-payments-dlq.mp4) · tag：`step-04`

## 这一章解决什么问题？

付款结果是异步来的：支付方回调我们，我们把事件放进 `stockhold-payment-events` 队列，payments Lambda 处理。
付款失败 → release；付款成功 → confirm。

难的是失败的那些消息。一条消息可能：

- 根本不是 JSON（上游 bug）；
- 引用了一个不存在的 hold；
- 下游临时挂了，过一会儿就好；
- hold 已经过期了，付款才到（这需要人去退款）。

第一种和第三种，单看一次处理是分不清的。所以统一的做法是：**每条失败的消息重试有限次，超过 `maxReceiveCount` 就进死信队列（DLQ），由人来看。**

## 假设：一条坏消息会被收三次，然后出现在 DLQ 里？

队列的 `visibilityTimeout = 5s`、`maxReceiveCount = 3`（`infra/app.mjs`，锚点 `cdk-payments`）。
Lambda 用 `reportBatchItemFailures`：一批里只有坏的那条被退回，好的照常删掉。

handler（`stockhold/payments.py`，锚点 `payment-handler`）只做三件事：解析、看故障开关、调 release / confirm。
失败的时候，Lambda 把这一次尝试追加到 `FAIL#<message_id>` 这一行：第几次收到、错误码、时间。
这样到了 DLQ 里，重放工具不用重跑，就能说出「它为什么在这里」。

## 怎么稳定地造出一条坏消息？

`stockhold/faults.py`：故障开关存在 DynamoDB 里，`arm(hold_id, 3)` 表示「这个 hold 接下来三次处理都失败」，每失败一次减一。
为什么按次数，不按时间？因为 `maxReceiveCount` 正好是 3：三次失败进 DLQ，开关也正好用完，之后重放一定成功。演示和测试都是确定的。
没有全局开关，故意的：全局故障会误伤同一个环境里别人的演示。

## 真实跑一次

`tests/test_payments.py::test_bad_messages_are_retried_then_dead_lettered_with_a_reason`（锚点 `test-poison-dlq`）一次发四种坏消息。
故障那一条，Lambda 在 CloudWatch Logs 里留下的三行：

```
{"fn": "payments", "message_id": "674cbd86-…", "receive_count": 1, "error": "DOWNSTREAM_UNAVAILABLE", "detail": "injected fault: the order service is down"}
{"fn": "payments", "message_id": "674cbd86-…", "receive_count": 2, "error": "DOWNSTREAM_UNAVAILABLE", …}
{"fn": "payments", "message_id": "674cbd86-…", "receive_count": 3, "error": "DOWNSTREAM_UNAVAILABLE", …}
```

测试断言：四条都在 DLQ 里；每条的失败记录正好是第 1、2、3 次；错误码分别是 `MALFORMED_EVENT`、`HOLD_NOT_FOUND`、`DOWNSTREAM_UNAVAILABLE`、`HOLD_EXPIRED`；
那个 hold 还是 HELD，没有被处理一半。

## 重复的付款事件呢？

支付方的 webhook 也会重试。`test_duplicate_messages_on_the_queue_release_once` 往队列里放四条一样的 `payment_failed`：
release 是带条件的（第二章），所以库存只回来一次，history 里只有一条 RELEASED。

## 怎么 checkout 到这一步跑起来

```bash
git checkout step-04
./scripts/deploy.sh && .venv/bin/pytest          # 48 passed，DLQ 那条要等十几秒
```
