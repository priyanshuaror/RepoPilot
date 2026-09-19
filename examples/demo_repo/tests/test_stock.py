from inventory.stock import OutOfStockError, available, reserve


def test_available_defaults_to_zero():
    assert available("widget", {}) == 0


def test_reserve_reduces_stock():
    assert reserve("widget", 2, {"widget": 5}) == {"widget": 3}


def test_reserve_rejects_oversized_reservation():
    try:
        reserve("widget", 9, {"widget": 5})
    except OutOfStockError:
        return
    raise AssertionError("expected OutOfStockError")
