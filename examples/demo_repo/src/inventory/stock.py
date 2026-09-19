"""Stock levels for the warehouse."""


class OutOfStockError(Exception):
    """Raised when a reservation cannot be satisfied."""


def available(item, inventory):
    """Units of `item` currently on the shelf."""
    return inventory.get(item, 0)


def reserve(item, quantity, inventory):
    """Reserve `quantity` units of `item`, returning the updated inventory.

    NOTE: this does not currently reject negative quantities, which is the bug
    the demo issue describes.
    """
    if available(item, inventory) < quantity:
        raise OutOfStockError(f"only {available(item, inventory)} units of {item} left")
    updated = dict(inventory)
    updated[item] = updated.get(item, 0) - quantity
    return updated
