<!-- .venv/bin/python scripts/simulate_traffic.py --buyers 150 --hold-seconds 20 --arrival-seconds 40  (run a334), then scripts/analyze.py --sku-like a334 -->

### rates_by_product

| sku | holds | confirmed | released | expired | still_held | expiry_rate | release_rate |
|---|---|---|---|---|---|---|---|
| sim-sneaker-a334 | 55 | 21 | 13 | 21 | 0 | 0.382 | 0.236 |
| sim-tee-a334 | 52 | 38 | 3 | 11 | 0 | 0.212 | 0.058 |
| sim-mug-a334 | 43 | 40 | 2 | 1 | 0 | 0.023 | 0.047 |

### wait_before_confirm

| sku | confirmed | p50_s | p90_s | p99_s | max_s |
|---|---|---|---|---|---|
| sim-mug-a334 | 40 | 2.0 | 4.3 | 7.3 | 7.8 |
| sim-sneaker-a334 | 21 | 4.3 | 14.1 | 18.5 | 19.3 |
| sim-tee-a334 | 38 | 3.2 | 7.8 | 10.6 | 10.7 |

### hold_timeout_recommendation

| sku | current_hold_s | p99_pay_s | recommended_hold_s | expired |
|---|---|---|---|---|
| sim-mug-a334 | 20 | 7.3 | 9 | 1 |
| sim-sneaker-a334 | 20 | 18.5 | 23 | 21 |
| sim-tee-a334 | 20 | 10.6 | 13 | 11 |

### release_window

| sku | actor | expired | p50_ms | p99_ms | max_ms |
|---|---|---|---|---|---|
| sim-mug-a334 | expiry-message | 1 | 1133.0 | 1133.0 | 1133 |
| sim-sneaker-a334 | expiry-message | 21 | 1296.0 | 2080.0 | 2114 |
| sim-tee-a334 | expiry-message | 11 | 1032.0 | 1408.0 | 1416 |
