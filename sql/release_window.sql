-- The eventual-release window: how long after expiresAt a hold really became EXPIRED, and who did it.
SELECT sku,
       actor,
       count(*)                                              AS expired,
       round(quantile_cont(lag_after_expiry_ms, 0.50))       AS p50_ms,
       round(quantile_cont(lag_after_expiry_ms, 0.99))       AS p99_ms,
       max(lag_after_expiry_ms)                              AS max_ms
FROM events
WHERE to_status = 'EXPIRED'
GROUP BY sku, actor
ORDER BY sku, actor
