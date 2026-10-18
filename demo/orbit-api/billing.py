"""Synthetic annual billing fixture."""
from decimal import Decimal


def annual_total(monthly_price: Decimal) -> Decimal:
    if monthly_price < 0:
        raise ValueError("Price must not be negative")
    return (monthly_price * Decimal("12") * Decimal("0.90")).quantize(Decimal("0.01"))
