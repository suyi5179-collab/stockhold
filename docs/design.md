# stockhold design notes

中文为主，关键术语保留英文。数字全部是这个仓库在本机 LocalStack 上实测的，原始输出在 `docs/results/`。

## 1. 一致性取舍：最终释放（eventual release），不用同步锁

### 问题

hold 有一个截止时间 `expires_at_ms`。到了时间没人付款，库存要还回去。
两种做法：

| | 同步锁 / 同步检查 | 最终释放（本仓库） |
|---|---|---|
| 怎么做 | 下单时加锁，或者每次读库存时顺手检查「哪些 hold 过期了」并当场释放 | reserve 时投一条 SQS 延迟消息，到点由 expire Lambda 释放；每分钟一个 sweeper 兜底 |
| 读库存 | 每次都要扫过期 hold，读变成写 | 只读库存行，一次 GetItem / Query |
| 锁 | 付款期间要持有锁或租约；进程挂了锁怎么办 | 没有锁；所有状态变化都是条件写 |
| 代价 | 热门商品上的锁就是瓶颈 | 过期之后到真正释放之间，有一个**窗口期**，这段时间里那几件库存「不可卖」 |

### 窗口期会不会造成错误？

不会卖错，只会少卖一会儿：

- 窗口期里，hold 还是 HELD，库存没回来，别人买不到这几件。这是**可用性**损失，不是正确性问题。
- 窗口期里，原来的客户还想付款怎么办？confirm 的条件里有 `expires_at_ms > :now`（`stockhold/inventory.py`，锚点 `transition-guard`），过期就是 `409 HOLD_EXPIRED`。所以「过期了还能付款成功」不会发生，哪怕 expire 消息还没到。
- expire 和 confirm 同时到：两个都是 `status = HELD AND version = :v` 的条件写，只有一个能提交。

### 最坏情况是什么？

- 延迟消息丢了 / expire Lambda 一直失败：消息重试五次进 `stockhold-hold-expiry-dlq`，sweeper 每分钟扫 GSI1（只有 HELD 的 hold 在里面），把超过 `expiresAt + 5s` 的 hold 过期掉。窗口期上限约等于 `宽限期 + sweeper 周期` = 5 秒 + 1 分钟。
- sweeper 也停了：库存一直被占着，不会超卖，但会少卖。告警看的是「HELD 且已经过期」的数量（第八章）。
- SQS 延迟最长 900 秒。hold 超过 15 分钟时，消息提前到达，handler 看到还没到时间，就重新投一条延迟消息（`rescheduled`），一跳一跳接力。

### 实测窗口有多长？

`scripts/measure_expiry_lag.py`：一次占 30 个 hold、每个 3 秒，看每个 hold 实际变成 EXPIRED 比 `expiresAt` 晚多少毫秒。

| 配置 | min | p50 | p90 | max |
|---|---|---|---|---|
| LocalStack 默认（空队列时 poller 最多退避 10 秒） | 5416 | 7056 | 8459 | 8510 |
| 退避上限改成 1 秒，部署后第一次跑（Lambda 冷启动） | 4205 | 5349 | 6599 | 6651 |
| 同上，Lambda 已热 | 1018 | 2478 | 3926 | 3981 |

剩下的一到四秒来自：LocalStack 的延迟消息大约晚 0.75 秒才可见；30 条消息分成 3 批，event source mapping 一批一批串行调用。
这是 LocalStack 上的数，不代表 AWS 上的数。定位过程见 [problems.md 的 q02](problems.md)。
