-- How long buyers take to pay: the distribution of hold -> CONFIRMED, per product.
SELECT sku,
       count(*)                                   AS confirmed,
       round(quantile_cont(wait_ms, 0.50) / 1000, 1) AS p50_s,
       round(quantile_cont(wait_ms, 0.90) / 1000, 1) AS p90_s,
       round(quantile_cont(wait_ms, 0.99) / 1000, 1) AS p99_s,
       round(max(wait_ms) / 1000, 1)                  AS max_s
FROM events
WHERE to_status = 'CONFIRMED'
GROUP BY sku
ORDER BY sku
