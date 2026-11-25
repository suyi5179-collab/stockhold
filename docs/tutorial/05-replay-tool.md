# 05 DLQ 重放工具：搜得到、看得懂、重放得安全

> 视频：[p05-replay.mp4](../videos/p05-replay.mp4) · tag：`step-05`

## 这一章解决什么问题？

消息进了 DLQ 以后，总得有人处理。半夜被叫起来的 on-call 工程师要回答四个问题：

1. 这条消息是什么、为什么失败？（搜索 + 失败记录）
2. 它说的那个 hold 现在是什么状态？（对比 payload）
3. 重放了会发生什么？（dry-run）
4. 重放会不会出事：重复释放、两个人同时点、手滑点了两次？（安全重放）

这一章是项目的「全栈」部分：Flask + 原生 JavaScript / HTML / CSS，不用 React。

## 为什么要先「归档」，不直接看 SQS？

SQS 不能「看一眼」：想看消息就得 receive，receive 会让它的接收次数加一，还会把它藏起来一段时间。
所以工具做的第一件事是 `sync()`（`stockhold/dlq.py`，锚点 `dlq-archive`）：把 DLQ 里的消息收出来，连同第四章记下的每次失败原因，写进 DynamoDB 的 `DLQ` 分区，**写成功之后再删** SQS 里那一份。
中间崩了怎么办？下一次 sync 会再归档一次，按 message id 判重，是空操作。
Flask 进程里有一个后台线程每三秒 sync 一次，所以告警是几秒钟级别的新鲜度。

## 搜索和对比

- 搜索（锚点 `dlq-search`）：自由文本匹配 hold id、sku、message id、原文；加上原因、状态、时间范围三个过滤。
- 原消息 vs 当前 hold：消息里写的 `hold_id / order_id / sku / qty`，和 hold 现在的值逐个比；再加一行「期望是 HELD，现在是什么」。
- 两条消息互相对比：勾两条，按字段 diff。

## dry-run 为什么是一个纯函数？

`plan()`（锚点 `dry-run`）只读不写，返回「可以重放：HELD → RELEASED，分片 0 的 available 499 → 500」，或者「不能重放」加原因：

| 情况 | dry-run 的回答 |
|---|---|
| 畸形 JSON | 修生产方，重放没有用 |
| hold 不存在 | 不能重放 |
| hold 已经不是 HELD | 重放什么都不会改变；如果是「过期后才付款」，要退款，不是重放 |
| 故障开关还没用完 | 可以，但会警告：重放还会失败 |

## 重放怎么保证最多一次？

三层（锚点 `replay`）：

1. **重新算一次 plan**。dry-run 和点重放之间状态可能变了。
2. **认领**：把归档行从 `OPEN` 条件写成 `REPLAYING`（`ConditionExpression: status = OPEN`）。五个人同时点，只有一个能写成功，其余拿到 `409 ALREADY_REPLAYED`。
3. **release 本身是条件写**（第二章）。就算重放的消息被投递两次，库存也只回来一次。

每次 dry-run、重放、被拒绝的重放都写一条审计记录（谁、什么时候、计划是什么、新消息 id）。
只有 `oncall` 角色能重放（锚点 `require-role`、`replay-endpoint`）；`viewer` 能搜、能看、能 dry-run。

## 真实跑一次

`tests/test_replay.py::test_replay_is_dry_run_first_oncall_only_and_at_most_once`（锚点 `test-replay-safety`）：

```
viewer  POST /ops/api/messages/<id>/replay   -> 403 FORBIDDEN
oncall  5 threads POST …/replay at once      -> [200, 409, 409, 409, 409]
hold    HELD -> RELEASED (once), available back to 5, audit: replay ×1, replay-refused ×4, dry-run ×1
```

界面上（`/ops`，账号 `oncall / oncall-demo`）：点「下游故障三次」，十五秒左右它出现在列表里，原因 `DOWNSTREAM_UNAVAILABLE`，收到三次；点开看三次失败；dry-run；重放。

## 真实踩到的坑

页面上「收到次数」一开始显示 4，可明明只失败了三次。原因是归档自己 receive 的那一次也算进了 `ApproximateReceiveCount`。归档时减一。

## 怎么 checkout 到这一步跑起来

```bash
git checkout step-05
./scripts/demo.sh            # http://127.0.0.1:8840/ops
.venv/bin/pytest             # 57 passed
```
