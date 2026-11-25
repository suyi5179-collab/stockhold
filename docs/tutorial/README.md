# stockhold 分章教程

先做出最小能跑的版本，再一步步加功能。每章对应一个 git tag，`git checkout step-NN` 就能回到那一步，那一步的测试是绿的。

| 章 | tag | 这一章解决什么 | 视频 |
|---|---|---|---|
| [01 最小版本：预留库存，绝不超卖](01-reserve.md) | `step-01` | 条件写、状态机、并发测试和第一个失败对照 | [p01-reserve](../videos/p01-reserve.mp4) |
| [02 幂等](02-idempotency.md) | `step-02` | Idempotency-Key、重复消息只释放一次、第二个失败对照 | [p02-idempotency](../videos/p02-idempotency.mp4) |
| [03 过期释放](03-expiry.md) | `step-03` | 延迟消息、expire Lambda、sweeper、CDK、窗口期实测 | [p03-expiry](../videos/p03-expiry.mp4) |
| [04 付款事件与死信](04-payments-dlq.md) | `step-04` | 重试、DLQ、每次失败的原因、故障开关 | [p04-payments-dlq](../videos/p04-payments-dlq.mp4) |
| [05 DLQ 重放工具](05-replay-tool.md) | `step-05` | 搜索、对比、dry-run、最多一次的重放、角色、审计、告警 | [p05-replay](../videos/p05-replay.mp4) |
| [06 热点 key 与分片](06-hot-key-sharding.md) | `step-06` | 写分片、1 vs 8 分片压测、结账模拟页 | [p06-sharding](../videos/p06-sharding.mp4) |
| [07 事件日志与 SQL](07-event-log.md) | `step-07` | Streams → S3、DuckDB 代替 Athena、hold 时长建议 | [p07-eventlog](../videos/p07-eventlog.mp4) |
| [08 观测](08-observability.md) | `step-08` | 穿过队列的 trace、指标、告警、仪表盘 | [p08-observability](../videos/p08-observability.mp4) |
| [09 教学模式](09-learn-mode.md) | `step-09` | 真实状态驱动的阶段时间线、固定 commit 的源码链接、录屏 | [p09-learn](../videos/p09-learn.mp4) |
