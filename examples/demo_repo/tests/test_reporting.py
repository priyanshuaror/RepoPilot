from inventory.reporting import low_stock


def test_low_stock_lists_items_at_or_below_threshold():
    assert low_stock({"widget": 2, "bolt": 50}) == ["widget"]
