"""Tests for isotonic.py -- the monotone fit applied to a Kalshi threshold
ladder before it gets differenced into bucket probabilities."""
import pytest

from isotonic import isotonic_nonincreasing


def _is_nonincreasing(xs, tol=1e-12):
    return all(a + tol >= b for a, b in zip(xs, xs[1:]))


def test_empty_and_singleton():
    assert isotonic_nonincreasing([]) == []
    assert isotonic_nonincreasing([0.5]) == [0.5]


def test_already_monotone_is_untouched():
    xs = [0.9, 0.7, 0.4, 0.1]
    assert isotonic_nonincreasing(xs) == pytest.approx(xs)


def test_docstring_example():
    """The worked example in isotonic.py's own docstring -- if this drifts,
    the docstring is lying to the next reader."""
    out = isotonic_nonincreasing([0.82, 0.68, 0.72, 0.51])
    assert out == pytest.approx([0.82, 0.70, 0.70, 0.51])


def test_pools_violating_pair_into_weighted_average():
    out = isotonic_nonincreasing([0.6, 0.8], weights=[3.0, 1.0])
    expected = (0.6 * 3 + 0.8 * 1) / 4
    assert out == pytest.approx([expected, expected])


def test_strictly_increasing_input_collapses_to_the_mean():
    """Worst case: nothing is salvageable, so the closest non-increasing
    curve is the flat weighted mean."""
    out = isotonic_nonincreasing([0.1, 0.2, 0.3, 0.4])
    assert out == pytest.approx([0.25] * 4)


def test_output_is_monotone_and_mass_preserving_on_random_input():
    import random

    rng = random.Random(0)
    for _ in range(200):
        n = rng.randint(2, 25)
        xs = [rng.random() for _ in range(n)]
        ws = [rng.uniform(0.1, 5.0) for _ in range(n)]
        out = isotonic_nonincreasing(xs, ws)
        assert len(out) == n
        assert _is_nonincreasing(out)
        # PAVA is a weighted-least-squares projection: it preserves the
        # weighted mean of the input.
        assert sum(o * w for o, w in zip(out, ws)) == pytest.approx(
            sum(x * w for x, w in zip(xs, ws))
        )


def test_beats_local_clipping_on_a_noisy_kink():
    """The behaviour this module was written to fix: local clipping only
    patches the violating pair, PAVA fits the whole neighbourhood."""
    xs = [0.90, 0.50, 0.55, 0.20]
    out = isotonic_nonincreasing(xs)
    assert _is_nonincreasing(out)
    assert out[1] == pytest.approx(0.525)
    assert out[2] == pytest.approx(0.525)
