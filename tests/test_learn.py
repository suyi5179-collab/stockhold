"""Teaching mode backend: stage status comes from the real stores, and the docs tabs render."""
import time

from stockhold import learn, learn_docs


def test_a_sample_order_reaches_every_stage_in_order(api):
    r = api.post("/learn/api/start", json={"scenario": "payment_failed"})
    assert r.status_code == 201
    run_id, seen, t0 = r.json["run_id"], [], time.time()
    while time.time() - t0 < 90:
        st = api.get(f"/learn/api/runs/{run_id}").json
        for s in st["stages"]:
            if s["status"] == "done" and s["id"] not in seen:
                seen.append(s["id"])
        if len(seen) == len(learn.MAIN):
            break
        time.sleep(1)
    assert seen == [s["id"] for s in learn.MAIN]
    by = {s["id"]: s for s in st["stages"]}
    assert by["reserve"]["data"]["reserve"]["available_after"] == by["reserve"]["data"]["reserve"]["available_before"] - 2
    assert by["lambda"]["data"]["transition"]["to"] == "RELEASED" and by["lambda"]["data"]["transition"]["by"] == "payment-events"
    assert any(o["event"]["to_status"] == "RELEASED" for o in by["s3"]["data"]["objects"])
    assert by["duckdb"]["data"]["rates"]["rows"][0]["sku"] == learn.LEARN_SKU
    assert api.post("/learn/api/start", json={"scenario": "nope"}).status_code == 400


def test_docs_tabs_render_with_pinned_source_links(api):
    iv = learn_docs.interview()
    assert sum(len(g["items"]) for g in iv["groups"]) >= 25
    assert "/blob/" in iv["groups"][0]["items"][0]["html"] and 'data-anchor="state-machine"' in iv["groups"][0]["items"][0]["html"]
    pr = learn_docs.problems()
    assert len(pr["items"]) == 8 and all(p["anchors"] for p in pr["items"])
    assert "日常问题与解决" in api.get("/learn/problems").data.decode()
    assert api.get("/videos/p01-reserve.mp4").status_code in (200, 404)
