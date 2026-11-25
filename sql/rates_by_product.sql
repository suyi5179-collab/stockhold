-- Expiry and release rates by product. Athena: the same SELECT over an external table on s3://stockhold-events/events/.
-- @learn:sql-rates
WITH final AS (
  SELECT sku, hold_id, arg_max(to_status, at_ms) AS status
  FROM events GROUP BY sku, hold_id
)
SELECT sku,
       count(*)                                              AS holds,
       count(*) FILTER (WHERE status = 'CONFIRMED')          AS confirmed,
       count(*) FILTER (WHERE status = 'RELEASED')           AS released,
       count(*) FILTER (WHERE status = 'EXPIRED')            AS expired,
       count(*) FILTER (WHERE status = 'HELD')               AS still_held,
       round(count(*) FILTER (WHERE status = 'EXPIRED')  / count(*), 3) AS expiry_rate,
       round(count(*) FILTER (WHERE status = 'RELEASED') / count(*), 3) AS release_rate
FROM final
GROUP BY sku
ORDER BY holds DESC, sku
-- @learn:end sql-rates
