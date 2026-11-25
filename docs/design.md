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

## 2. 访问模式 → 键设计

单表 `stockhold`，按需计费。每个访问模式都是一次 GetItem / Query，没有 Scan（`stockhold/keys.py`，CDK 在 `infra/app.mjs` 锚点 `cdk-table`）。

| 访问模式 | 谁用 | 键 | 操作 |
|---|---|---|---|
| 读商品元数据（容量、分片数） | API、报表 | `PK=SKU#<sku>`, `SK=META` | GetItem |
| 读一个商品的全部库存分片 | reserve（每次尝试前） | `PK=SKU#<sku>`, `SK begins_with SHARD#` | Query（强一致） |
| 扣 / 还一个分片的库存 | reserve、release、expire | `PK=SKU#<sku>`, `SK=SHARD#<k>` | 事务内条件 Update |
| 按 id 读写 hold | 所有转移、幂等重放 | `PK=HOLD#<hold_id>`, `SK=HOLD`；`hold_id = sha256(client, Idempotency-Key)` | GetItem、条件 Put / Update |
| 找过期了还开着的 hold | sweeper | GSI1：`gsi1pk=HELD#<0..3>`, `gsi1sk=expires_at_ms`，**稀疏**（离开 HELD 时 REMOVE） | 4 次 Query（`gsi1sk < now − 5s`） |
| 一个商品的全部 hold | 库存报表、不变式检查、结账页 | GSI2：`gsi2pk=SKU#<sku>#<k>`, `gsi2sk=created_at_ms` | 每个分片一次 Query |
| 付款事件的失败记录 | payments Lambda 写、重放工具读 | `PK=FAIL#<message_id>`, `SK=FAIL` | Update（list_append）、GetItem |
| 死信归档 / 审计 | 重放工具 | `PK=DLQ`, `SK=<archived_at_ms>#<message_id>`；`PK=AUDIT`, `SK=<at_ms>#<id>`；按 message id 找：`PK=DLQIDX#<id>` | Query（倒序）、条件 Update（认领） |
| 故障开关、用户、教学运行记录 | 演练、登录、教学模式 | `FAULT#<hold_id>`、`USER#<name>`、`LEARN#<run_id>` | GetItem / 条件 Update |

### 热点风险检查

| 键 | 风险 | 处理 |
|---|---|---|
| `SKU#<秒杀商品> / SHARD#k` | 开抢那一秒所有写入落在一个 item 上 | 写分片：热门商品 N 个分片，reserve 随机选够这一单的分片（第 3 节实测） |
| GSI2 | 如果按 `SKU#<sku>` 建索引，秒杀商品的每个 hold 都写同一个索引分区，GSI 被限流会反压基表 | 索引键也带分片号 |
| GSI1 | 「所有开着的 hold」是一个天然的热分区 | 分成 4 个桶（`HELD#<hash % 4>`），sweeper 逐桶查 |
| `PK=DLQ` / `PK=AUDIT` | 单分区，按时间追加 | 死信和审计的量远小于下单量，演示规模下可以接受；量大了按日期分桶（写在 README 的局限里） |
| `HOLD#<id>` | 均匀散开 | 无 |

## 3. 热点 key：实测而不是声称

LocalStack 不模拟分区吞吐上限，所以这里**测不到也不声称**「没有限流」。能测的是同一个 item 上的乐观并发冲突。
`scripts/bench_shards.py`：400 件、400 单、32 线程，三轮（`docs/results/bench_shards.md`）：

| 分片 | 占到 | 冲突放弃 | 每单冲突 | p99 ms |
|---|---|---|---|---|
| 1 | 252 / 254 / 252 | 148 / 146 / 148 | 4.29 / 4.31 / 4.21 | 4313 / 3239 / 2372 |
| 8 | 399 / 400 / 400 | 1 / 0 / 0 | 1.62 / 1.49 / 1.54 | 2193 / 1443 / 3069 |

分片的代价：一单要从一个分片里拿够；卖到最后几件时，库存散在各片里。合并分片没有实现。

## 4. 部分失败

| 中间态 | 能不能出现 | 怎么办 |
|---|---|---|
| 扣了库存、没写 hold（或反过来） | 不能：同一个 `TransactWriteItems` | — |
| hold 改了状态、库存没还 | 不能：release / expire 的两半在同一个事务里 | — |
| hold 写了、延迟消息没发 | 能（进程死在两步之间） | 同 key 重试时 API 补发；没人重试时 sweeper 过期它（`test_sweeper_expires_a_hold_whose_message_was_lost`） |
| 死信归档写了、SQS 里没删 | 能 | 下次 sync 按 message id 判重，空操作 |
| 重放的认领写了、消息没发出去 | 能（状态停在 REPLAYING） | 审计里看得到；没有自动恢复，需要人工把归档行改回 OPEN（局限） |
