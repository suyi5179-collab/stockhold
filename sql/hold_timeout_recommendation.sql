-- Hold timeout per product: long enough for 99% of the buyers who do pay, plus 20%,
-- and how many of today's expiries a timeout that long would still have expired.
-- @learn:sql-recommend
WITH pay AS (
  SELECT sku, quantile_cont(wait_ms, 0.99) AS p99_ms, max(hold_seconds) AS current_s
  FROM events WHERE to_status = 'CONFIRMED' GROUP BY sku
), exp AS (
  SELECT sku, count(*) AS expired FROM events WHERE to_status = 'EXPIRED' GROUP BY sku
)
SELECT pay.sku,
       pay.current_s                                  AS current_hold_s,
       round(pay.p99_ms / 1000, 1)                    AS p99_pay_s,
       ceil(pay.p99_ms * 1.2 / 1000)::INTEGER         AS recommended_hold_s,
       coalesce(exp.expired, 0)                       AS expired
FROM pay LEFT JOIN exp USING (sku)
ORDER BY pay.sku
-- @learn:end sql-recommend
