"""Write sharding for hot products: same total, spread over N items; no oversell in a flash sale."""
from stockhold import flash, inventory
from stockhold.states import InsufficientStock

from .conftest import login


def test_shards_hold_the_total_and_sell_out_exactly(new_sku):
    sku = new_sku(23, shards=8)
    rep = inventory.stock_report(sku)
    assert [s["capacity"] for s in rep["shards"]] == [3, 3, 3, 3, 3, 3, 3, 2]
    for i in range(23):
        inventory.reserve(sku, 1, f"o-{i}")
    rep = inventory.stock_report(sku)
    assert rep["available"] == 0 and rep["held"] == 23 and all(s["available"] == 0 for s in rep["shards"])
    try:
        inventory.reserve(sku, 1, "o-late")
        raise AssertionError("sold out, should refuse")
    except InsufficientStock:
        pass


def test_an_order_must_fit_in_one_shard(new_sku):
    """A known trade-off of sharding: 4 units left in total, but no single shard has 2."""
    sku = new_sku(8, shards=8)
    for i in range(4):
        inventory.reserve(sku, 1, f"o-{i}")
    try:
        inventory.reserve(sku, 2, "o-big")
        raise AssertionError("expected INSUFFICIENT_STOCK")
    except InsufficientStock as e:
        assert e.detail["available"] == 4


# @learn:test-flash-sale
def test_flash_sale_on_eight_shards_never_oversells(new_sku):
    sku = new_sku(60, shards=8)
    r = flash.run(sku, orders=100, concurrency=24)
    assert r["oversold"] == 0 and r["balanced"]
    assert r["held"] + r["sold_out"] + r["contention"] == 100
    assert r["held"] == 60 - r["after"]["available"]
    assert len(r["per_shard"]) == 8                       # every shard took orders
# @learn:end test-flash-sale


def test_checkout_page_and_flash_endpoint(api, users):
    assert api.get("/checkout").status_code == 302
    login(api, users, "oncall")
    assert "并发下单" in api.get("/checkout").data.decode()
    rep = api.post("/checkout/api/new-sku", json={"units": 30, "shards": 4}).json
    r = api.post("/checkout/api/flash", json={"sku": rep["sku"], "orders": 40, "concurrency": 8}).json
    assert r["oversold"] == 0 and r["held"] <= 30 and r["after"]["sku"] == rep["sku"]
    assert api.post("/checkout/api/flash", json={"sku": rep["sku"], "orders": 99999}).status_code == 400
