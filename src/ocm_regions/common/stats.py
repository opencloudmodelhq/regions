# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Small statistics helpers."""

import math
from typing import Final

Z_95: Final = 1.96


def wilson(successes: int, total: int, z: float = Z_95) -> tuple[float, float]:
    """Wilson score interval for a proportion, which stays sensible at 0% and 100%.

    Args:
        successes: Number of successes.
        total: Number of items in all.
        z: Normal quantile for the interval.

    Returns:
        The lower and upper bound, each between 0 and 1.
    """
    if not total:
        return 0.0, 1.0
    share, z2 = successes / total, z * z
    centre = share + z2 / (2 * total)
    margin = z * math.sqrt(share * (1 - share) / total + z2 / (4 * total * total))
    denominator = 1 + z2 / total
    low = 0.0 if successes == 0 else (centre - margin) / denominator
    high = 1.0 if successes == total else (centre + margin) / denominator
    return low, high
