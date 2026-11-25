# Burst runs that overloaded LocalStack (kept because they are what docs/problems.md q04/q05 are about)

All: `scripts/simulate_traffic.py --buyers 150 --hold-seconds 12`, buyers arriving within 5 s (the old default).

| run | change | confirmed / 150 | dead-lettered payment events | first attempt after send |
|---|---|---|---|---|
| 2a93 | LocalStack mem_limit 1100m | 1 | — | LocalStack's main process was OOM-killed mid-run: `ConnectionClosedError`, `docker inspect` oom=true |
| 3d27 | mem_limit 1536m | 30 | 39, all `INVALID_TRANSITION` after a first `HOLD_EXPIRED` | p50 10.5 s, max 15.2 s |
| 1bae | payments handler runs a batch's records in parallel | 23 | 42 (37 INVALID_TRANSITION, 5 HOLD_EXPIRED) | (not better) |
| 3e9d | batch 50 + 1 s batching window | 17 | — | (worse; reverted) |
| a334 | buyers arrive over 40 s, hold 20 s | 99 | 0 | see analysis_sim_a334.md |

Release window of run 3d27 (expiry messages queued behind the burst):

| sim-sneaker-3d27 | expiry-message | 29 | 10015.0 | 13509.0 | 13633 |
| sim-sneaker-3d27 | sweeper | 17 | 7808.0 | 8869.0 | 8947 |
| sim-tee-3d27 | expiry-message | 32 | 12294.0 | 14893.0 | 14898 |
| sim-tee-3d27 | sweeper | 9 | 7804.0 | 8912.0 | 8952 |
