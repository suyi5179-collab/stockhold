# 03 过期释放：延迟消息 + sweeper，不用锁

> 视频：[p03-expiry.mp4](../videos/p03-expiry.mp4) · tag：`step-03`

## 这一章解决什么问题？

客户把东西放进购物车，占了库存，然后关掉了页面。没人付款，这几件库存就永远被占着。
所以 hold 要有截止时间，到点自动还回去。问题是：**谁来还？什么时候还？**

## 为什么不在读库存的时候顺手检查？

那样每次读库存都变成「扫一遍过期 hold 再写回去」，读变成了写，热门商品上并发一高就全是冲突。
也不能加锁等付款：付款可能要几十秒，锁在这么长时间里就是瓶颈，进程挂了锁还要回收。

我们选的是最终释放（eventual release）。取舍写在 [docs/design.md 第一节](../design.md)。

## 假设：投一条延迟到期的消息，到点就会被处理？

reserve 成功以后，API 往 `stockhold-hold-expiry` 队列投一条消息，`DelaySeconds = 剩余秒数`（锚点 `schedule-expiry`）。
expire Lambda 收到以后（锚点 `expire-handler`）：

- hold 不在了 → `missing`（reserve 根本没提交）；
- 已经不是 HELD → `already-confirmed` 之类，什么都不做（重复消息也走这里）；
- 还没到时间（超过 15 分钟的 hold） → 再投一条，`rescheduled`；
- 到时间了 → `expire()`，和 release 一样是带条件的事务。

## 真实跑一次

`tests/test_expiry.py::test_delayed_message_expires_the_hold_through_lambda`（锚点 `test-expiry-e2e`）：
真的经过 LocalStack 里的 SQS 和 Lambda 容器。Lambda 打到 CloudWatch Logs 里的那一行：

```
{"fn": "expire", "message_id": "e5bec6dd-624a-476e-8a55-ce573176192f", "hold_id": "h_a7cf39eaf9fb45d68d07", "outcome": "expired"}
```

## 消息丢了怎么办？

reserve 已经提交、延迟消息还没发出去，进程就挂了。这是一个「部分失败」。两道兜底：

1. 同一个请求重试（同一个 Idempotency-Key）时，API 发现这个 hold 没有 `expiry_message`，就补发一条（锚点 `api-schedule`）。
2. sweeper（锚点 `sweeper`）每分钟由 EventBridge 触发，查 GSI1。GSI1 是稀疏索引：只有 HELD 的 hold 有 `gsi1pk`，一旦离开 HELD 就 `REMOVE`。所以 sweeper 只读到还开着的 hold，过期超过 5 秒的就过期掉。

`test_sweeper_expires_a_hold_whose_message_was_lost`（锚点 `test-sweeper`）专门不发消息，验证 sweeper 能收拾，而且收拾完它就离开了索引。

## 窗口期有多长？真实踩到的坑

第一次量（`scripts/measure_expiry_lag.py`，30 个 3 秒的 hold）：

```
lag after expiresAt ms: min=5416 p50=7056 p90=8459 max=8510 mean=7000
```

三秒的 hold，平均晚了七秒才释放。排查下来，大头是 LocalStack 的 event source mapping 在队列空的时候退避，最多 10 秒才再拉一次。
把 `LAMBDA_EVENT_SOURCE_MAPPING_MAX_BACKOFF_ON_EMPTY_POLL_SEC` 设成 1 以后，Lambda 热的时候：

```
lag after expiresAt ms: min=1018 p50=2478 p90=3926 max=3981 mean=2459
```

## 部署：CDK

队列、DLQ（重试五次）、两个 Lambda、EventBridge 规则都写在 `infra/app.mjs`（锚点 `cdk-expiry`），`./scripts/deploy.sh` 用 cdklocal 部署到 LocalStack。
Lambda 的代码包由 `scripts/build_lambda.sh` 拼出来：handler 加上 `stockhold/` 里只依赖 boto3 的核心模块，测试和 Lambda 跑的是同一份代码。

## 怎么 checkout 到这一步跑起来

```bash
git checkout step-03
docker-compose -p stockhold up -d --wait && ./scripts/deploy.sh
.venv/bin/pytest                                   # 42 passed
.venv/bin/python scripts/measure_expiry_lag.py     # 窗口期
```
