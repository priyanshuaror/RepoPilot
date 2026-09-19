from inventory.orders import cancel_order, place_order


def test_place_order_reserves_stock():
    order, inventory = place_order("widget", 2, {"widget": 5})
    assert order["status"] == "placed"
    assert inventory["widget"] == 3


def test_cancel_order_restores_stock():
    order, inventory = place_order("widget", 2, {"widget": 5})
    _, inventory = cancel_order(order, inventory)
    assert inventory["widget"] == 5
