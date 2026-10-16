"""
Money helpers. All amounts are INTEGER Rwandan francs (RWF has no minor unit in practice).
Rates are integer basis points (100 bps = 1%). No floating point anywhere.
"""


def bps_of(amount_rwf: int, bps: int) -> int:
    """amount * bps / 10000, rounded half up, using integer arithmetic only."""
    if amount_rwf < 0 or bps < 0:
        raise ValueError("amount and bps must be non-negative")
    return (amount_rwf * bps + 5000) // 10000


def format_rwf(amount) -> str:
    if amount is None:
        return "—"
    return f"RWF {int(amount):,}"
