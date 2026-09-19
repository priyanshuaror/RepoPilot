# Negative quantities are accepted when reserving stock

`reserve()` checks that there is *enough* stock, but never checks that the
requested quantity is positive. Reserving a negative quantity therefore
*increases* the shelf count instead of failing:

```python
>>> from inventory.stock import reserve
>>> reserve("widget", -10, {"widget": 5})
{'widget': 15}
```

The same hole is reachable through `place_order`, so an order for -10 widgets
silently creates stock out of nothing.

`reserve` should reject a quantity that is not strictly positive, and
`place_order` should surface that failure rather than swallowing it.
