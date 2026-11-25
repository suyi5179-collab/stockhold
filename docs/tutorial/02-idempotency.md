# 02 幂等：重试和重复消息不会多占、多还

> 视频：[p02-idempotency.mp4](../videos/p02-idempotency.mp4) · tag：`step-02`

## 这一章解决什么问题？

网络会超时。客户端没收到响应，就会重试；SQS 是至少一次投递（at-least-once），同一条消息可能被处理两次。
所以有两个方向都要防：

- **重复占**：同一个下单请求重试了三次，不能占三份库存。
- **重复还**：同一条「付款失败」消息来了八次，不能把库存还八次。

## 重试的请求怎么认出来？

客户端每个请求带一个 `Idempotency-Key` 头。hold 的 id 不是随机的，而是 `sha256(client, key)` 算出来的（`stockhold/keys.py` 的 `hold_id_for`）。
所以同一个 key 的重试，落在同一行 hold 上。reserve 事务里 hold 行的条件是 `attribute_not_exists(PK)`：第二次写不进去，事务整体取消，库存也不会扣。

取消之后怎么办？读出已经存在的那一行（锚点 `idempotent-replay`）：

- 请求内容一样（`request_hash` 相同）→ 返回原来那个 hold，HTTP 200，带 `"replayed": true`；
- 内容不一样 → `422 IDEMPOTENCY_KEY_REUSED`。同一个 key 不能拿来下另一单。

## 假设：同一个请求同时到十次？

十个线程拿同一个 key 同时 reserve。只有一个事务能把 hold 行写进去，其余九个事务被取消，原因里第二项是 `ConditionalCheckFailed`，于是都读到那一行返回。
`test_concurrent_retries_of_one_request_take_stock_once`：十个结果同一个 hold_id，库存只少了 3。

还有一个容易漏的情况：库存已经卖光，客户端这时候重试。如果先查库存，会返回「没货」，客户端以为下单失败。
所以 reserve 第一件事是先按 key 查 hold，查到就直接返回（`test_sold_out_retry_still_finds_its_hold`）。

## 重复还库存怎么防？

release / expire 的写法（锚点 `transition-guard` 和 `release-transact`）：

- hold 行：`SET status = :to`，条件 `status = HELD AND version = :v`；
- 库存行：`ADD available :qty`，和上面在同一个事务里。

hold 一旦不是 HELD，事务就取消，库存那一半也不会执行。输掉竞争的那一方重新读一次：如果状态已经是它想要的，就当作成功的空操作（`changed: false`）。

## 真实跑一次：关掉保护会怎样？

`test_duplicate_payment_failures_release_once`（锚点 `test-duplicate-release`）：一个 hold 占了 2 件，同一条释放消息八个线程同时处理。

打开保护：`available == 5`，只有一个 `changed: true`。
关掉保护（`STOCKHOLD_SABOTAGE=no_release_guard`，锚点 `release-unsafe`，两次不带条件的写）：

```
E       AssertionError: {'sku': 't-99a37701f1', 'capacity': 5, 'available': 19, 'held': 0, ...}
E       assert 19 == 5
1 failed, 6 passed
```

五件的商品，库存变成了十九件：八条消息里有七条都把 2 件还了回去。

## 真实踩到的坑

第一次写测试时，idempotency key 用的是固定字符串 `"k-1"`。第一次全绿，第二次跑就红了：

```
stockhold.states.IdempotencyKeyReused: this Idempotency-Key was already used for a different request
```

上一轮的 hold 还在表里，同一个 key 这次配的是另一个 SKU。这恰好说明 422 是对的。测试改成 key 里带上本轮的 SKU。

## 怎么 checkout 到这一步跑起来

```bash
git checkout step-02
./scripts/deploy.sh && .venv/bin/pytest            # 35 passed
STOCKHOLD_SABOTAGE=no_release_guard .venv/bin/pytest tests/test_idempotency.py   # 1 failed
```
