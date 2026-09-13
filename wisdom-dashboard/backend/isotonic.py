"""Weighted isotonic regression (Pool Adjacent Violators Algorithm), used to
enforce P(S_T > K) monotonically non-increasing in K before a threshold
ladder (Kalshi's KXBTCD -- see adapters/kalshi.py) gets differenced into a
histogram.

Why this exists: raw market quotes are noisy -- adjacent strikes can quote
implied "greater than" probabilities slightly out of order (e.g. P(>100k) =
0.42 but P(>110k) = 0.46, which is impossible since a higher bar can never
be more likely). Before this module, adapters/kalshi.py handled that by
clipping each difference to >= 0 locally (`max(0.0, p_lo - p_hi)`), which
only fixes the single violating pair and can distort the curve right around
it. PAVA instead finds the closest (weighted-least-squares) monotone curve
to the WHOLE sequence at once -- the standard, textbook-correct way to do
this (see e.g. Barlow et al., "Statistical Inference under Order
Restrictions") -- so a noisy kink gets smoothed into its neighborhood
rather than just zeroed out.
"""
from __future__ import annotations


def isotonic_nonincreasing(values: list[float], weights: list[float] | None = None) -> list[float]:
    """The closest non-increasing sequence to `values` (weighted least
    squares if `weights` given, else unweighted). O(n) stack-based PAVA.

    Example: isotonic_nonincreasing([0.82, 0.68, 0.72, 0.51]) pools the
    middle two (0.68, 0.72 -- a violation, 0.72 > 0.68) into their
    weighted average, giving [0.82, 0.70, 0.70, 0.51].
    """
    n = len(values)
    if n == 0:
        return []
    w = list(weights) if weights is not None else [1.0] * n

    # Each stack entry: [pooled_value, total_weight, n_points_in_block].
    # Adjacent blocks that violate non-increasing order get merged
    # (weighted-averaged) until the whole stack is properly ordered.
    stack: list[list[float]] = []
    for i in range(n):
        stack.append([values[i], w[i], 1])
        while len(stack) >= 2 and stack[-2][0] < stack[-1][0]:
            v2, w2, c2 = stack.pop()
            v1, w1, c1 = stack.pop()
            total_w = w1 + w2
            stack.append([(v1 * w1 + v2 * w2) / total_w, total_w, c1 + c2])

    out: list[float] = []
    for v, _, c in stack:
        out.extend([v] * c)
    return out
