"""Warehouse reporting. Unrelated to stock reservation."""


def low_stock(inventory, threshold=5):
    """Items at or below `threshold` units."""
    return sorted(item for item, count in inventory.items() if count <= threshold)
