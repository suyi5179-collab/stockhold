# 日常问题与解决

面试官常问：「你遇到过什么问题？怎么定位的？怎么解决的？」
下面八个问题，**六个是做这个项目时真的踩到的**（标「真实」），两个是这类系统在生产上的典型事故，用失败对照复现出来的（标「典型」）。
每题都有一段逐行高亮的讲解视频 `docs/videos/problems/<id>.mp4`，教学模式的「日常问题」标签页里也能看。

代码位置写成「文件（锚点 `名字`）」。在教学模式页面里，「看源码」会跳到 GitHub 上固定 commit 的那几行；修复前的写法如果已经不在主干里，就保存在 `docs/problems/snippets/`，同样有锚点。

## q01 第二次跑测试，幂等测试红了（真实）

**现象**
```
E           stockhold.states.IdempotencyKeyReused: this Idempotency-Key was already used for a different request
E       AssertionError: [IdempotencyKeyReused('this Idempotency-Key was already used for a different request'), …]
```
第一次跑全绿，第二次跑三个幂等测试红。

**在哪里**：[tests/test_idempotency.py](../tests/test_idempotency.py)（锚点 `test-unique-keys`），修复前的写法在 [docs/problems/snippets/before_fixes.py](problems/snippets/before_fixes.py)（锚点 `q01-before`）

**怎么定位**
- 假设一：代码坏了。代码没改过，同一份代码第一次是绿的，排除。
- 假设二：环境里有残留。表在两次运行之间不清空，上一轮用 `"k-1"` 建的 hold 还在，这一轮的 SKU 是新建的另一个。
- 验证：同一个 key、不同的请求体，服务按契约返回 422。服务是对的，测试是错的。

**怎么解决**
```python
# 修复前
a = inventory.reserve(sku, 2, "o-1", idempotency_key="k-1")
# 修复后
a = inventory.reserve(sku, 2, "o-1", idempotency_key=f"k-1-{sku}")
```

**怎么证明修好了**：连跑两次 `.venv/bin/pytest` 都是绿的；修复前第二次必红。

**面试怎么讲**：S 幂等测试第二次运行变红；T 分清是代码错还是测试错；A 发现是固定的 key 撞上了上一轮的数据，改成每轮唯一；R 测试稳定，而且这个红灯本身证明了「同 key 不同请求返回 422」这条规则是生效的。

## q02 三秒的 hold，七秒以后才释放（真实）

**现象**（`scripts/measure_expiry_lag.py`，原始输出 `docs/results/expiry_lag_before.txt`）
```
holds=30 hold_seconds=3 expired=30 by={'expiry-message': 30}
lag after expiresAt ms: min=5416 p50=7056 p90=8459 max=8510 mean=7000
```

**在哪里**：[docker-compose.yml](../docker-compose.yml)（锚点 `localstack-poll`），修复前 [docs/problems/snippets/before.yml](problems/snippets/before.yml)（锚点 `q02-before`）

**怎么定位**
- 假设一：SQS 延迟消息不准。单独发一条 `DelaySeconds=3` 的消息自己收，3.75 秒可见，只差 0.75 秒，不是大头。
- 假设二：Lambda 慢。CloudWatch 的 `REPORT` 行每次几百毫秒，但两次调用之间隔了好几秒。时间不在处理上，在「谁来拉」上。
- 验证：读 LocalStack 4.9 的 `config.py`，`LAMBDA_EVENT_SOURCE_MAPPING_MAX_BACKOFF_ON_EMPTY_POLL_SEC` 默认 10：队列空的时候轮询一直退避到十秒。

**怎么解决**
```yaml
# 修复前：没有这一项（默认 10 秒）
# 修复后
LAMBDA_EVENT_SOURCE_MAPPING_MAX_BACKOFF_ON_EMPTY_POLL_SEC: "1"
```

**怎么证明修好了**：同样的量法，Lambda 热的时候 `p50=2478 p90=3926 max=3981`（`docs/results/expiry_lag_after.txt`）。这是本机 LocalStack 的数，不代表 AWS。

**面试怎么讲**：S 最终释放的窗口期比设计的大很多；T 量出窗口由哪几段组成；A 把队列延迟、轮询、处理分开测，定位到模拟器的空轮询退避；R 窗口中位数从七秒降到两秒半，并且写进了设计文档。

## q03 失败三次，页面上显示收到四次（真实）

**现象**：重放工具里每条死信的「收到次数」都是 4，`maxReceiveCount` 却是 3，失败记录里也正好是第 1、2、3 次。

**在哪里**：[stockhold/dlq.py](../stockhold/dlq.py)（锚点 `dlq-receive-count`），修复前（锚点 `q03-before`）

**怎么定位**
- 假设一：重试多跑了一次。失败记录只有三条，排除。
- 假设二：数是在别处被加一的。归档要从 DLQ `receive_message` 一次，SQS 的 `ApproximateReceiveCount` 把这一次也算上了。

**怎么解决**
```python
# 修复前
"receive_count": int(m["Attributes"].get("ApproximateReceiveCount", "0")),
# 修复后
"receive_count": max(0, int(m["Attributes"].get("ApproximateReceiveCount", "1")) - 1),
```

**怎么证明修好了**：`tests/test_replay.py` 里死信的收到次数和失败记录一致；页面上显示 3。

**面试怎么讲**：S 值班工具显示的重试次数不对；T 弄清这个数到底数的是什么；A 对上失败记录，发现观测者自己的读取改变了计数，归档时扣掉；R 工具上的数和消费者真实的尝试次数一致。

## q04 顾客一起涌进来，付了款的单也过期了（真实）

**现象**（`docs/results/overload_runs.md`）：150 个顾客 5 秒内一起下单，hold 12 秒。只有 30 人付款成功，39 条付款事件进了死信：
```
{'code': 'HOLD_EXPIRED', 'receive_count': 1, 'message': 'hold h_b716a9075acebbcea5ac expired before it was confirmed'}
{'code': 'INVALID_TRANSITION', 'receive_count': 2, 'message': 'EXPIRED -> CONFIRMED is not allowed'}
first attempt after send (s): min 9.1 p50 10.5 max 15.2
```

**在哪里**：[stockhold/inventory.py](../stockhold/inventory.py)（锚点 `transition-guard`），[lambdas/payments.py](../lambdas/payments.py)（锚点 `payments-batch`），修复前的串行 handler 在 [docs/problems/snippets/payments_handler_serial.py](problems/snippets/payments_handler_serial.py)（锚点 `q-payments-serial`）

**怎么定位**
- 假设一：Lambda 一批十条串行处理太慢。改成批内并行，再跑：确认 23 人，没变好。排除。
- 假设二：每批拿得太少。改成一批 50 条加 1 秒攒批窗口：确认 17 人，更差。回退。
- 验证：两次都没用，说明瓶颈不在 Lambda。150 个线程同时抢单分片的商品，本机 DynamoDB 的每次调用都变慢，Lambda 只是跟着慢。这是容量问题，不是逻辑问题。

**怎么解决**：分两层。正确性：确认付款的条件里一直有 `expires_at_ms > :now`，过期后才到的付款一定确认不了，进死信，dry-run 写明「refund the customer instead」。容量：顾客分散到 40 秒内到达（更接近真实），并用第七章的 SQL 按付款时长的 p99 调 hold 时长。
```python
# 如果只写（修复前的样子，锚点 q04-before）
"ConditionExpression": "#s = :held AND version = :v",
# 现在
"ConditionExpression": "#s = :held AND version = :v" + (" AND expires_at_ms > :now" if to == states.CONFIRMED else ""),
```

**怎么证明修好了**：`tests/test_payments.py::test_bad_messages_are_retried_then_dead_lettered_with_a_reason` 故意让付款晚到，断言原因是 `HOLD_EXPIRED`、hold 没被确认；分散到达的一轮（run a334）150 人里 99 人付款成功、0 条死信。

**面试怎么讲**：S 流量突增时付了款的订单被过期；T 判断是代码问题还是容量问题；A 两次修法没用都留了记录，最后定位到本机存储饱和，正确性靠条件写守住，hold 时长交给数据；R 过期后的付款不会被错误确认，hold 时长有了按商品的建议值。

## q05 LocalStack 被 OOM 杀掉了（真实）

**现象**
```
botocore.exceptions.ConnectionClosedError: Connection was closed before we received a valid response from endpoint URL: "http://localhost:45671/".
$ docker inspect stockhold-localstack-1 --format '{{.State.OOMKilled}}'
true
```
容器状态仍是 Up，`ps` 里只剩 supervisor 和 Java。

**在哪里**：[docker-compose.yml](../docker-compose.yml)（锚点 `localstack-memory`），修复前（锚点 `q05-before`）

**怎么定位**
- 假设一：DynamoDB Local 的 Java 被杀。`ps` 里 Java 还在，被杀的是 LocalStack 主进程。
- 先把 `mem_limit` 从 1100m 提到 1536m，能跑了。但跑完测试又涨到 1.259 GiB。
- 按进程看 RSS：`601152 node …/kinesis-local/main.js`。最大的是 Kinesis 模拟器，它在给 DynamoDB Streams 供数（第七章）。

**怎么解决**
```yaml
# 修复前
mem_limit: 1100m
# 修复后
mem_limit: 1536m
DYNAMODB_HEAP_SIZE: 256m
NODE_OPTIONS: "--max-old-space-size=160"
KINESIS_PERSISTENCE: "0"
KINESIS_LATENCY: "100"
```

**怎么证明修好了**：全新环境跑完全部测试，Node 173 MB，容器 754 MiB（`docs/results/localstack_memory.md`）。

**面试怎么讲**：S 本地环境在压测时崩了；T 找到是哪个进程吃了内存；A 容器没退出也去查 OOM 标志，按进程看 RSS，给真正的大头加堆上限而不是一味加内存；R 同样的负载下内存降了四成多，两个项目共用的虚拟机没有再被挤爆。

## q06 给已有的表加 Stream，CDK 部署失败（真实）

**现象**
```
Stockhold | CREATE_FAILED | AWS::IAM::Policy | Eventlog/ServiceRole/DefaultPolicy
Resource provider operation failed: An error occurred (MalformedPolicyDocument) when calling the PutRolePolicy
operation: Resource unknown must be in ARN format or "*".
```

**在哪里**：[infra/app.mjs](../infra/app.mjs)（锚点 `cdk-table`），修复前（锚点 `q06-before`）

**怎么定位**
- 策略里的资源是表的 Stream ARN，CloudFormation 要等表更新完才知道它。
- LocalStack 在「更新已有的表、新加 Stream」这条路上，没有把 Stream ARN 交回给 CloudFormation，于是是 `unknown`。
- 新建的表带 Stream 一次就能成功，说明 CDK 代码本身没问题。

**怎么解决**：代码不变（`stream: NEW_AND_OLD_IMAGES`），改部署方式：删栈重建。删栈时上一次失败留下的 Lambda 没删掉，再部署报 `Function already exist: stockhold-expire`，最后 `docker-compose -p stockhold down -v` 清空 LocalStack 重来。

**怎么证明修好了**：最终验收就是 `down -v` 以后从零跑 `scripts/demo.sh`，一次部署成功，`tests/test_eventlog.py` 绿。

**面试怎么讲**：S 基础设施升级在本地模拟器上失败；T 分清是我的 IaC 写错了还是模拟器的差异；A 用「新建一次能成功」排除了代码问题，改用从零部署来验收；R 演示脚本在空环境上一次跑通，差异写进了 README 的局限。

## q07 不带条件写，十件卖出了三十件（典型）

**现象**（失败对照 `STOCKHOLD_SABOTAGE=no_condition`）
```
>       assert rep["oversold"] == 0, rep
E       assert 20 == 0
```

**在哪里**：[stockhold/inventory.py](../stockhold/inventory.py)（锚点 `reserve-transact`，对照路径锚点 `reserve-unsafe`），测试（锚点 `test-oversell`）

**怎么定位**：超卖二十件；每一单记录里的 `available_before` 都是 10：三十个人读到的是同一个值，写回的也是同一个值。

**怎么解决**
```python
# 失败对照：读出来、判断、写回算好的值
UpdateExpression="SET available = :a, version = :v"   # 没有 ConditionExpression
# 正常路径：在一个事务里减库存 + 写 hold
"UpdateExpression": "SET available = available - :n, version = version + :one",
"ConditionExpression": "available >= :n AND version = :v",
```

**怎么证明修好了**：同一个测试，打开保护 `oversold == 0`，关掉保护 `20 == 0` 红。

**面试怎么讲**：S 秒杀时同一件商品被多人买走；T 保证任何并发下都不超卖；A 用条件写加乐观并发代替锁，并留一个开关证明测试会红；R 仓库里的并发测试零超卖，对照组多卖二十件。

## q08 重复的付款失败消息，把库存还了七次（典型）

**现象**（失败对照 `STOCKHOLD_SABOTAGE=no_release_guard`）
```
E       AssertionError: {'sku': 't-99a37701f1', 'capacity': 5, 'available': 19, 'held': 0, ...}
E       assert 19 == 5
```

**在哪里**：[stockhold/inventory.py](../stockhold/inventory.py)（锚点 `transition-guard`、`release-transact`，对照路径锚点 `release-unsafe`），测试（锚点 `test-duplicate-release`）

**怎么定位**：19 − 5 = 14，每次还 2 件，正好七次多余的释放；八条一样的消息只有一条该生效。

**怎么解决**：释放的条件是 `status = HELD AND version = :v`，和「库存加回去」放在同一个事务里；输掉竞争的一方重新读，看到已经是 RELEASED 就返回 `changed: false`。

**怎么证明修好了**：打开保护 `available == 5`、只有一个 `changed: true`；关掉保护 `19 == 5` 红。

**面试怎么讲**：S 至少一次投递导致库存被多还；T 让每个改库存的消费者幂等；A 条件写 + 事务 + 输家重读；R 八条重复消息只生效一次，对照组多还了七次。
