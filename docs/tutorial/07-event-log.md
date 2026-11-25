# 07 事件日志：Streams → S3，再用 SQL 调 hold 时长

> 视频：[p07-eventlog.mp4](../videos/p07-eventlog.mp4) · tag：`step-07`

## 这一章解决什么问题？

hold 时长该设多久？设短了，付款慢一点的客户被过期，体验差，还会出现「过期后才付款」要退款；设长了，库存被占着卖不出去。
这个数不能拍脑袋，要看数据：每个商品的过期率、释放率，以及客户从下单到付款到底要多久。

## 事件从哪来？

不在业务代码里到处写日志。DynamoDB Streams 把表上每一次变更（新旧两份镜像）交给 eventlog Lambda（`infra/app.mjs`，锚点 `cdk-eventlog`）。
Lambda 只挑一种变更：hold 行，而且状态变了（锚点 `stream-to-event`）。像「记下延迟消息的 id」这种修改，状态没变，就跳过。

`tests/test_eventlog.py::test_hold_changes_land_in_s3_as_json_lines`（锚点 `test-eventlog-e2e`）走真的 Streams 和 Lambda：占住、释放，两行事件都出现在 S3 的日期分区里。

每个事件一行 JSON，写到 `s3://stockhold-events/events/dt=YYYY-MM-DD/hour=HH/<请求 id>.jsonl`。按日期和小时分区，查询只扫需要的那几天。

事件里带上分析要用的字段：`wait_ms`（从占住到这次变化用了多久）、`lag_after_expiry_ms`（过期事件比 expiresAt 晚了多少）。

## 为什么是 DuckDB？

AWS 上会用 Athena：在 S3 上建外部表，直接写 SQL。本地没有 Athena，DuckDB 能用同样的 SQL 读同样的 JSON Lines（`read_json_auto` 加 `hive_partitioning`，锚点 `duckdb-view`）。
`sql/` 下四条查询：

| 文件 | 回答什么 |
|---|---|
| `rates_by_product.sql`（锚点 `sql-rates`） | 每个商品的过期率、释放率 |
| `wait_before_confirm.sql` | 付款等待时长的 p50 / p90 / p99 |
| `hold_timeout_recommendation.sql`（锚点 `sql-recommend`） | 建议的 hold 时长 = 付款 p99 × 1.2 |
| `release_window.sql` | 最终释放的窗口期：过期之后多久才真正释放，谁释放的 |

`scripts/analyze.py` 先把 S3 上的事件镜像到 `.runtime/eventlog/`，再跑这四条。

## 假设：模拟一批顾客，SQL 能告诉我们 hold 该设多久？

`scripts/simulate_traffic.py`：三个商品，每个商品的顾客行为不同（付款、付款失败、走掉的比例，付款平均要几秒）。所有动作都走真的路径：reserve、延迟消息、付款事件队列、Lambda。

## 真实跑一次（run a334：150 个顾客，40 秒内陆续到达，hold 20 秒）

```
rates_by_product
| sim-sneaker-a334 | 55 | 21 | 13 | 21 | 0 | 0.382 | 0.236 |
| sim-tee-a334     | 52 | 38 |  3 | 11 | 0 | 0.212 | 0.058 |
| sim-mug-a334     | 43 | 40 |  2 |  1 | 0 | 0.023 | 0.047 |

hold_timeout_recommendation
| sku              | current_hold_s | p99_pay_s | recommended_hold_s | expired |
| sim-mug-a334     | 20 |  7.3 |  9 |  1 |
| sim-sneaker-a334 | 20 | 18.5 | 23 | 21 |
| sim-tee-a334     | 20 | 10.6 | 13 | 11 |
```

怎么读：球鞋的顾客付款慢（p99 十八秒半），二十秒的 hold 对它偏短，建议二十三秒；杯子的顾客两秒就付完，九秒就够，多出来的十一秒都是被白占的库存。
完整输出：`docs/results/analysis_sim_a334.md`。注意这是脚本按设定的比例造出来的顾客，它证明的是「这条链路和这几条 SQL 能算出答案」，不是哪个真实商店的数据。

## 真实踩到的坑：顾客一起涌进来，付了款的单也过期了

第一版模拟器让 150 个顾客在 5 秒内全部下单。结果：

- LocalStack 容器的内存上限 1100 MB，主进程被 OOM 杀掉，所有请求报 `ConnectionClosedError`。上限改到 1536 MB。
- 再跑一次，150 个人里只有 30 个付款成功，39 条付款事件进了死信，原因都是 `HOLD_EXPIRED`（付款到的时候 hold 已经过期了）。付款事件从发出到 Lambda 第一次处理，中位数等了 10.5 秒。

这就是「过期后才付款」：在 AWS 上它意味着要给客户退款。排查过程和两次没用的修法在 [problems.md 的 q04](../problems.md)。

## 怎么 checkout 到这一步跑起来

```bash
git checkout step-07
docker-compose -p stockhold up -d --wait && ./scripts/deploy.sh
.venv/bin/python scripts/simulate_traffic.py --buyers 150 --hold-seconds 20 --arrival-seconds 40
.venv/bin/python scripts/analyze.py --sku-like <run>
.venv/bin/pytest tests/test_eventlog.py
```
