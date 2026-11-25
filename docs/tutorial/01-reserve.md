# 01 最小版本：预留库存，绝不超卖

> 视频：[p01-reserve.mp4](../videos/p01-reserve.mp4) · tag：`step-01`

## 这一章解决什么问题？

下单和付款之间有一段时间。这段时间里，库存要先「占住」（hold），不然付完款才发现没货了。
占库存最容易出的错是**超卖**（oversell）：两个人同时看到「还剩 1 件」，两个人都下单成功。

这一章只做最小的东西：一个商品、一行库存、一个 hold 的状态机，还有一个能证明「不超卖」的测试。

## hold 有哪几种状态？

四种，而且只能从 HELD 出发走一步：

```
HELD → CONFIRMED   付款成功
HELD → RELEASED    付款失败 / 客户取消
HELD → EXPIRED     到时间没人付
```

终态（CONFIRMED / RELEASED / EXPIRED）不能再动。非法转移返回 `409 INVALID_TRANSITION`。
转移表在 `stockhold/states.py`（锚点 `state-machine`），错误码在 [docs/api.md](../api.md)。

## 假设：三十个人同时抢十件，会怎样？

如果按「先读库存、够就写回去」来做，大家读到的都是 10，都写回 9，最后会卖出远超十件。
我们的假设是：只要写的时候带上条件，DynamoDB 只会让条件仍然成立的那一个写成功。

## 条件写长什么样？

reserve 是一个 `TransactWriteItems`，两件事要么一起成，要么一起不成（`stockhold/inventory.py`，锚点 `reserve-transact`）：

1. 库存行：`SET available = available - :n, version = version + 1`，条件 `available >= :n AND version = :v`；
2. hold 行：`Put`，条件 `attribute_not_exists(PK)`。

条件里的 `version = :v` 是乐观并发（optimistic concurrency）：我读到的是第 v 版，写的时候如果已经不是 v 了，说明别人先写了，我重新读、再试一次。
冲突次数记在 hold 的 `reserve.conflicts` 里，后面第六章拿它做压测。

## 真实跑一次

```
POST /v1/skus {"sku":"tee-fff6","total":5}                     -> 201
POST /v1/reservations {"sku":"tee-fff6","qty":2,"order_id":"o-1001"}
201 {"hold_id":"h_697a…","status":"HELD","qty":2,
     "reserve":{"available_before":5,"available_after":3,"version_before":0,"version_after":1,"attempts":1,"conflicts":0}}
POST /v1/reservations {"sku":"tee-fff6","qty":9,"order_id":"o-1002"}
409 {"error":{"code":"INSUFFICIENT_STOCK","detail":{"available":3,"requested":9}}}
```

## 怎么证明它真的不超卖？

`tests/test_reserve.py::test_concurrent_checkouts_never_oversell`（锚点 `test-oversell`）：三十个线程在 barrier 上同时起跑，抢十件。

- 打开保护：`10 held, 0 contention`，`oversold == 0`，库存恒等式 `available + held + confirmed == capacity` 成立。
- 关掉保护再跑（`STOCKHOLD_SABOTAGE=no_condition`，走 `_reserve_unsafe`，锚点 `reserve-unsafe`）：

```
>       assert rep["oversold"] == 0, rep
E       assert 20 == 0
1 failed
```

三十个人全部「成功」，多卖了二十件。测试会红，说明它是活的。

## 怎么 checkout 到这一步跑起来

```bash
git checkout step-01
docker-compose -p stockhold up -d --wait
./scripts/deploy.sh                      # cdklocal 部署 DynamoDB 表
.venv/bin/pytest                         # 25 passed
STOCKHOLD_SABOTAGE=no_condition .venv/bin/pytest -k oversell   # 1 failed
```
