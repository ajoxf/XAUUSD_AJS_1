"""Seasonal long-bias multiplier — spec §2."""

SEASONAL_MULTIPLIER = {
    1: 1.10, 2: 1.10, 3: 1.05,
    4: 0.95, 5: 0.90, 6: 0.90,
    7: 0.90, 8: 0.95, 9: 1.00,
    10: 1.05, 11: 1.10, 12: 1.10,
}


def seasonal_multiplier(month: int, direction: str) -> float:
    """Returns ±10% multiplier for LONG signals only. Shorts always get 1.0."""
    if direction.upper() != "LONG":
        return 1.0
    if month not in SEASONAL_MULTIPLIER:
        raise ValueError(f"invalid month {month}")
    return SEASONAL_MULTIPLIER[month]
