import pytest

from stockhold import keys, states
from stockhold.states import CONFIRMED, EXPIRED, HELD, RELEASED, InvalidTransition


@pytest.mark.parametrize("to", [CONFIRMED, RELEASED, EXPIRED])
def test_held_moves_to_every_terminal_state(to):
    assert states.check_transition(HELD, to) in ("confirm", "release", "expire")


@pytest.mark.parametrize("frm", sorted(states.TERMINAL))
@pytest.mark.parametrize("to", [HELD, CONFIRMED, RELEASED, EXPIRED])
def test_terminal_states_never_move(frm, to):
    with pytest.raises(InvalidTransition) as e:
        states.check_transition(frm, to)
    assert e.value.code == "INVALID_TRANSITION" and e.value.status == 409


def test_error_payload_is_stable():
    e = states.InsufficientStock("not enough", requested=3, available=1)
    assert e.to_dict() == {"error": {"code": "INSUFFICIENT_STOCK", "message": "not enough",
                                     "detail": {"requested": 3, "available": 1}}}


def test_split_stock_keeps_the_total():
    for total in (0, 1, 7, 100, 1001):
        for shards in (1, 3, 8):
            split = keys.split_stock(total, shards)
            assert sum(split) == total and len(split) == shards and max(split) - min(split) <= 1
    with pytest.raises(ValueError):
        keys.split_stock(10, 0)


def test_key_formats():
    assert keys.sku_pk("tee") == "SKU#tee"
    assert keys.shard_sk(3) == "SHARD#03"
    assert keys.sku_shard_key("tee", 3) == "SKU#tee#03"
    assert keys.held_bucket("h_abc") == keys.held_bucket("h_abc")
    assert {keys.held_bucket(f"h_{i}") for i in range(200)} == {f"HELD#{n}" for n in range(keys.HELD_BUCKETS)}
