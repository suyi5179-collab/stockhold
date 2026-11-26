# 讲解视频

每个教学阶段一段短片，`/learn` 页面里每个阶段旁边的「看讲解视频」播放的就是这里的文件。清单见 [`video/parts.json`](../../video/parts.json)：

| 文件 | 内容 | 对应教程 |
|---|---|---|
| [`p01-reserve.mp4`](p01-reserve.mp4) | 下单：条件写扣库存，绝不超卖 | [01](../tutorial/01-reserve.md) |
| [`p02-idempotency.mp4`](p02-idempotency.mp4) | 幂等：重复的消息只释放一次 | [02](../tutorial/02-idempotency.md) |
| [`p03-expiry.mp4`](p03-expiry.mp4) | 过期释放：延迟消息和 sweeper | [03](../tutorial/03-expiry.md) |
| [`p04-payments-dlq.mp4`](p04-payments-dlq.mp4) | 付款失败、重试、进死信 | [04](../tutorial/04-payments-dlq.md) |
| [`p05-replay.mp4`](p05-replay.mp4) | DLQ 重放工具：dry-run 和最多一次的重放 | [05](../tutorial/05-replay-tool.md) |
| [`p06-sharding.mp4`](p06-sharding.mp4) | 热点 key：一个分片对八个分片 | [06](../tutorial/06-hot-key-sharding.md) |
| [`p07-eventlog.mp4`](p07-eventlog.mp4) | 事件日志进 S3，DuckDB 算过期率 | [07](../tutorial/07-event-log.md) |
| [`p08-observability.mp4`](p08-observability.mp4) | 一条 trace 穿过队列，告警在工具顶部 | [08](../tutorial/08-observability.md) |
| [`p09-learn.mp4`](p09-learn.mp4) | 教学模式本身：源码锚点、面试题、日常问题 | [09](../tutorial/09-learn-mode.md) |

画面由 `video/record.mjs` 在真实页面上录制（会话里的数据都是这次真实请求产生的），红框标出每一步该看的地方；旁白来自 `video/narration.json`。旁白是用作者本人声音在本地克隆合成的（CosyVoice 2，不调任何云端 API），成片用本地 whisper 逐段回验过念的是稿子上的话。

## 日常问题（`problems/`）

每个问题一段代码讲解：先看修复前的写法和现象，再看修复后的写法，讲到哪几行就用红框框出哪几行。内容来自 [`video/problems.walk.json`](../../video/problems.walk.json)（由 `scripts/build_problem_videos.py` 按锚点从源码生成），问题的来龙去脉见 [`docs/problems.md`](../problems.md)。

- [`q01-fixed-idempotency-key.mp4`](problems/q01-fixed-idempotency-key.mp4) — 第二次跑测试，幂等测试红了
- [`q02-expiry-seven-seconds-late.mp4`](problems/q02-expiry-seven-seconds-late.mp4) — 三秒的 hold，七秒以后才释放
- [`q03-receive-count-four.mp4`](problems/q03-receive-count-four.mp4) — 失败三次，页面上显示收到四次
- [`q04-paid-after-expiry.mp4`](problems/q04-paid-after-expiry.mp4) — 顾客一起涌进来，付了款的单也过期了
- [`q05-localstack-oom.mp4`](problems/q05-localstack-oom.mp4) — LocalStack 被 OOM 杀掉了
- [`q06-stream-on-existing-table.mp4`](problems/q06-stream-on-existing-table.mp4) — 给已有的表加 Stream，CDK 部署失败
- [`q07-oversell.mp4`](problems/q07-oversell.mp4) — 不带条件写，十件卖出了三十件
- [`q08-double-release.mp4`](problems/q08-double-release.mp4) — 重复的付款失败消息，把库存还了七次
