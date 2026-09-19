"""Order placement, built on top of stock reservations."""

from inventory.stock import OutOfStockError, reserve


def place_order(item, quantity, inventory):
    """Place an order, returning (order, updated_inventory)."""
    updated = reserve(item, quantity, inventory)
    return {"item": item, "quantity": quantity, "status": "placed"}, updated


def cancel_order(order, inventory):
    """Return an order's units to the shelf."""
    updated = dict(inventory)
    updated[order["item"]] = updated.get(order["item"], 0) + order["quantity"]
    return {**order, "status": "cancelled"}, updated


__all__ = ["place_order", "cancel_order", "OutOfStockError"]
